"""FastAPI application: phone-first HTML pages + JSON API (docs/design/api.md).

Run:  uvicorn loan_tracker.web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import secrets
import time
from collections import defaultdict, deque
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

from loan_tracker import auth, reports, services as svc
from loan_tracker.config import Settings, get_settings, today as clock_today
from loan_tracker.db import models as m
from loan_tracker.db.session import session_factory
from loan_tracker.web import formatting as fmt
from loan_tracker.web.schemas import ContactIn, EntryIn, FundingIn, LoanIn, PartyIn, ReverseIn, describe

HERE = Path(__file__).resolve().parent
BOOKS = {"all": None, "business": "BUSINESS", "personal": "PERSONAL"}
PUBLIC_PATHS = ("/login", "/static", "/healthz", "/manifest.webmanifest", "/sw.js")


class LoginRequired(Exception):
    pass


def create_app(settings: Settings | None = None, *, maker: sessionmaker | None = None,
               today_fn: Callable[[], date] | None = None) -> FastAPI:
    settings = settings or get_settings()
    # API docs are served below behind the login, not publicly
    app = FastAPI(title="Loan Tracker", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.maker = maker or session_factory(settings.database_url)
    app.state.today = today_fn or (lambda: clock_today(settings))
    app.state.failed_logins = defaultdict(deque)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                       https_only=settings.secure_cookies, max_age=12 * 3600)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters.update(inr=fmt.inr, dmy=fmt.dmy, dshort=fmt.dshort, pct=fmt.pct, rupees_input=fmt.rupees_input)
    templates.env.filters.update(when=lambda dt: fmt.when(dt, settings.timezone))
    templates.env.globals.update(chart_paths=fmt.chart_paths, donut_style=fmt.donut_style,
                                 action_labels=fmt.ACTION_LABELS, kind_labels=fmt.KIND_LABELS,
                                 outcome_labels=fmt.OUTCOME_LABELS, promise_labels=fmt.PROMISE_LABELS)
    insecure_secret = (len(settings.secret_key) < 32 or "change-me" in settings.secret_key
                       or settings.secret_key.startswith("dev-only"))

    # ------------------------------------------------------------------ deps
    def db(request: Request) -> Iterator[Session]:
        session = request.app.state.maker()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def log(request: Request, s: Session, action: str, entity: str | None = None,
            entity_id: int | None = None, actor: str | None = None, **detail) -> None:
        svc.audit(s, actor or request.session.get("user") or "unknown", action, entity, entity_id,
                  ip=request.client.host if request.client else None, **detail)

    def receipt_of(s: Session, entry_id: int):
        return s.scalar(select(m.Receipt).where(m.Receipt.ledger_entry_id == entry_id))

    def issuer_for(book: str) -> dict:
        name = (settings.receipt_business_name if book == "BUSINESS" else "") or settings.receipt_name
        return {"name": name, "details": settings.receipt_details}

    def receipt_json(r) -> dict:
        return {k: (str(v) if isinstance(v, (date, datetime)) else v) for k, v in {
            "id": r.id, "number": r.number, "loan_id": r.loan_id, "party": r.loan.party.name,
            "ledger_entry_id": r.ledger_entry_id, "issued_on": r.issued_on, "payment_date": r.payment_date,
            "amount_paise": r.amount_paise, "penalty_paise": r.penalty_paise, "interest_paise": r.interest_paise,
            "principal_paise": r.principal_paise, "credit_paise": r.credit_paise,
            "principal_left_paise": r.principal_left_paise, "interest_left_paise": r.interest_left_paise,
            "payment_mode": r.payment_mode, "reference": r.reference,
            "cancelled": r.cancelled_at is not None, "cancel_reason": r.cancel_reason}.items()}

    def user(request: Request) -> str:
        name = request.session.get("user")
        if not name:
            raise LoginRequired()
        return name

    def today() -> date:
        return app.state.today()

    def book_of(request: Request) -> str:
        q = (request.query_params.get("book") or request.cookies.get("book") or "all").lower()
        return q if q in BOOKS else "all"

    def csrf_token(request: Request) -> str:
        tok = request.session.get("csrf")
        if not tok:
            tok = request.session["csrf"] = secrets.token_urlsafe(24)
        return tok

    async def check_csrf(request: Request) -> None:
        """Every HTML form POST carries the session's token (defence in depth on
        top of SameSite=Lax cookies). JSON /api calls are covered by FastAPI's
        JSON content-type requirement, which a cross-site form cannot send."""
        form = await request.form()
        sent = form.get("csrf_token", "")
        if not sent or not secrets.compare_digest(str(sent), request.session.get("csrf", "")):
            raise svc.ServiceError("This form expired. Please try again.")

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        nav_overdue = 0
        if request.session.get("user"):
            with app.state.maker() as s2:
                nav_overdue = s2.scalar(select(func.count()).select_from(m.Loan).where(m.Loan.status == "OVERDUE")) or 0
        ctx.update(request=request, book=book_of(request), today=app.state.today(),
                   flash=request.session.pop("flash", None), error=request.session.pop("error", None),
                   user=request.session.get("user"), csrf=csrf_token(request),
                   nav_overdue=nav_overdue, insecure_secret=insecure_secret)
        return templates.TemplateResponse(request, name, ctx)

    def safe_path(url: str | None, default: str = "/") -> str:
        """Only ever redirect within this site: keep the path (and query) of
        the URL; anything not starting with a single '/' falls back to default."""
        if not url:
            return default
        parts = urlsplit(url)
        path = parts.path or "/"
        if not path.startswith("/") or path.startswith("//") or "\\" in path:
            return default
        return path + (f"?{parts.query}" if parts.query else "")

    def referer(request: Request, default: str = "/") -> str:
        ref = request.headers.get("referer")
        if ref:
            if urlsplit(ref).netloc not in ("", request.url.netloc):
                return default
        return safe_path(ref, default)

    def back(request: Request, url: str, *, flash: str | None = None, error: str | None = None):
        if flash:
            request.session["flash"] = flash
        if error:
            request.session["error"] = error
        return RedirectResponse(safe_path(url), status_code=303)

    def parse_date(v: str | None, default: date) -> date:
        if not v:
            return default
        try:
            return date.fromisoformat(v)
        except ValueError as exc:
            raise svc.ServiceError(f"not a date: {v!r}") from exc

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "login required"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        if not request.url.path.startswith("/api/"):   # HTML form: back to the page with a message
            request.session["error"] = "Please check the form: " + describe(errors)
            return RedirectResponse(referer(request), status_code=303)
        return JSONResponse({"detail": describe(errors),
                             "errors": [{"field": ".".join(str(x) for x in e.get("loc", ()) if x != "body"),
                                         "message": e.get("msg")} for e in errors]}, status_code=400)

    @app.exception_handler(svc.ServiceError)
    async def _service_error(request: Request, exc: svc.ServiceError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": str(exc)}, status_code=400)
        request.session["error"] = str(exc)
        return RedirectResponse(referer(request), status_code=303)

    # ------------------------------------------------------------- public
    @app.get("/healthz")
    def healthz(s: Session = Depends(db)):
        last = s.scalar(select(m.JobRun).order_by(m.JobRun.started_at.desc()).limit(1))
        return {"ok": True, "last_job_run": last.run_date.isoformat() if last else None,
                "last_job_ok": last.ok if last else None}

    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse({
            "name": "Loan Tracker", "short_name": "Loans", "start_url": "/", "display": "standalone",
            "background_color": "#ffffff", "theme_color": "#1f4e79",
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }, media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        return Response((HERE / "static" / "sw.js").read_text(), media_type="application/javascript")

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        return render(request, "login.html")

    @app.post("/login", dependencies=[Depends(check_csrf)])
    def login(request: Request, username: str = Form(...), password: str = Form(...),
              code: str = Form(""), s: Session = Depends(db)):
        ip = request.client.host if request.client else "?"
        fails = app.state.failed_logins[ip]
        now = time.time()
        while fails and now - fails[0] > 900:
            fails.popleft()
        if len(fails) >= 5:
            log(request, s, "LOGIN_BLOCKED", actor=username.strip()[:100])
            return back(request, "/login", error="Too many attempts. Wait 15 minutes.")
        u = s.scalar(select(m.AppUser).where(m.AppUser.username == username.strip()))
        ok = u is not None and auth.verify_password(password, u.password_hash)
        if ok and u.totp_secret:
            ok = auth.verify_totp(u.totp_secret, code)
        if not ok:
            fails.append(now)
            log(request, s, "LOGIN_FAILED", actor=username.strip()[:100])
            return back(request, "/login", error="Wrong username, password or code.")
        fails.clear()
        request.session.clear()  # new session on login (prevents session fixation)
        request.session["user"] = u.username
        log(request, s, "LOGIN_OK", "app_user", u.id)
        return RedirectResponse("/", status_code=303)

    @app.get("/logout")
    def logout(request: Request, s: Session = Depends(db)):
        if request.session.get("user"):
            log(request, s, "LOGOUT")
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ------------------------------------------------------------- pages
    @app.get("/book/{name}")
    def set_book(name: str, request: Request, _: str = Depends(user)):
        resp = RedirectResponse(referer(request), status_code=303)
        resp.set_cookie("book", name if name in BOOKS else "all", max_age=365 * 86400, samesite="lax")
        return resp

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        dash = reports.dashboard(s, today(), BOOKS[book_of(request)], cash_buffer_paise=settings.cash_buffer_paise,
                                 concentration_flag_pct=settings.concentration_flag_pct)
        last = s.scalar(select(m.JobRun).order_by(m.JobRun.started_at.desc()).limit(1))
        return render(request, "home.html", d=dash, last_job=last)

    @app.get("/parties", response_class=HTMLResponse)
    def parties_page(request: Request, q: str = "", _: str = Depends(user), s: Session = Depends(db)):
        rows = reports_parties(s, today(), BOOKS[book_of(request)], q)
        return render(request, "parties.html", rows=rows, q=q)

    @app.post("/parties", dependencies=[Depends(check_csrf)])
    def parties_create(request: Request, name: str = Form(...), phone: str = Form(""),
                       address: str = Form(""), notes: str = Form(""), _: str = Depends(user),
                       s: Session = Depends(db)):
        p = svc.create_party(s, name, phone=phone, address=address, notes=notes)
        log(request, s, "PARTY_CREATED", "party", p.id, name=p.name)
        return back(request, f"/parties/{p.id}", flash=f"Added {p.name}.")

    @app.get("/parties/{party_id}", response_class=HTMLResponse)
    def party_page(party_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        party = svc.get_party(s, party_id)
        rows = svc.portfolio(s, today(), BOOKS[book_of(request)], party_id=party_id)
        return render(request, "party.html", party=party, rows=rows, summary=party_summary(rows))

    @app.post("/parties/{party_id}", dependencies=[Depends(check_csrf)])
    def party_update(party_id: int, request: Request, name: str = Form(...), phone: str = Form(""),
                     address: str = Form(""), id_proof_ref: str = Form(""), guarantor: str = Form(""),
                     notes: str = Form(""), risk_tag: str = Form(""), _: str = Depends(user),
                     s: Session = Depends(db)):
        svc.update_party(s, party_id, name=name, phone=phone, address=address, id_proof_ref=id_proof_ref,
                         guarantor=guarantor, notes=notes, risk_tag=risk_tag)
        log(request, s, "PARTY_UPDATED", "party", party_id, name=name)
        return back(request, f"/parties/{party_id}", flash="Saved.")

    @app.get("/loans", response_class=HTMLResponse)
    def loans_page(request: Request, direction: str = "", status: str = "", q: str = "",
                   _: str = Depends(user), s: Session = Depends(db)):
        all_rows = svc.portfolio(s, today(), BOOKS[book_of(request)])
        rows = [r for r in all_rows
                if (not direction or r.loan.direction == direction)
                and (not status or r.status == status)
                and (not q or q.lower() in r.loan.party.name.lower() or q.strip().upper() in (f"LN-{r.loan.id}", str(r.loan.id)))]
        return render(request, "loans.html", rows=rows, all_rows=all_rows, direction=direction,
                      status=status, q=q)

    @app.get("/loans/new", response_class=HTMLResponse)
    def loan_new(request: Request, party_id: int | None = None, direction: str = "GIVEN",
                 _: str = Depends(user), s: Session = Depends(db)):
        parties = s.scalars(select(m.Party).order_by(m.Party.name)).all()
        return render(request, "loan_new.html", parties=parties, party_id=party_id, direction=direction)

    @app.post("/loans", dependencies=[Depends(check_csrf)])
    def loan_create(request: Request, party_id: int = Form(...), direction: str = Form(...),
                    book: str = Form(""), principal: str = Form(...), start_date: str = Form(""),
                    rate: str = Form(...), rate_unit: str = Form("PER_MONTH"),
                    interest_cycle: str = Form("MONTHLY"), due_day: str = Form(""),
                    grace_days: int = Form(3), maturity_date: str = Form(""), purpose: str = Form(""),
                    collateral: str = Form(""), payment_mode: str = Form("CASH"),
                    _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        loan = svc.create_loan(
            s, party_id=party_id, direction=direction, book=book,
            principal_paise=svc.rupees_to_paise(principal), start_date=parse_date(start_date, t),
            rate=svc.parse_rate(rate), rate_unit=rate_unit, interest_cycle=interest_cycle,
            due_day=int(due_day) if due_day else None, grace_days=grace_days,
            maturity_date=parse_date(maturity_date, None) if maturity_date else None,
            purpose=purpose, collateral=collateral, payment_mode=payment_mode, as_of=t)
        log(request, s, "LOAN_CREATED", "loan", loan.id, direction=loan.direction, book=loan.book,
            principal_paise=loan.principal_paise, rate=loan.rate)
        return back(request, f"/loans/{loan.id}", flash="Loan created.")

    @app.get("/loans/{loan_id}", response_class=HTMLResponse)
    def loan_page(loan_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        loan = svc.get_loan(s, loan_id)
        res = svc.compute_loan(loan, today())
        reversed_ids = {e.reverses_id for e in loan.entries if e.reverses_id}
        allocs: dict[int, list] = defaultdict(list)
        for a in res.allocations:
            allocs[a.entry_id].append(a)
        links = [l for l in svc.funding_links(s) if loan_id in (l.taken_loan_id, l.given_loan_id)]
        others = {l2.id: l2 for l2 in s.scalars(select(m.Loan).where(
            m.Loan.id.in_([l.taken_loan_id for l in links] + [l.given_loan_id for l in links])))}
        acts, proms, wa = [], [], None
        if loan.direction == "GIVEN":
            from loan_tracker.collections import reminder_text, wa_link
            acts, promises = svc.loan_collections(s, loan_id)
            proms = [(p, svc.promise_state(p, loan, today())) for p in promises]
            late = [d for d in res.dues if d.status == "OVERDUE"]
            pend_p, pending = next(((p, st) for p, st in sorted(proms, key=lambda x: x[0].promised_date)
                                    if st.status == "PENDING"), (None, None))
            wa = wa_link(loan.party.phone, reminder_text(
                loan.party.name, loan.id,
                overdue_paise=sum(d.outstanding_paise for d in late),
                overdue_since=late[0].due.due_date if late else None,
                next_paise=res.projected_due.interest_paise if res.projected_due else 0,
                next_date=res.projected_due.due_date if res.projected_due else None,
                promise_paise=pending.short_paise if pending else 0,
                promise_date=pend_p.promised_date if pend_p else None))
        rcpts = {r.ledger_entry_id: r for r in svc.receipts(s, loan_id=loan_id)}
        missing = len(svc.payments_without_receipt(s, loan_id)) if loan.direction == "GIVEN" else 0
        live_status = loan.status if loan.status in ("SETTLED", "WRITTEN_OFF") else res.loan_status
        can_close = loan.direction == "GIVEN" and live_status in ("CLOSED", "SETTLED")
        return render(request, "loan.html", loan=loan, r=res, reversed_ids=reversed_ids, allocs=allocs,
                      links=links, others=others, acts=acts, proms=proms, wa=wa, rcpts=rcpts,
                      missing_receipts=missing, can_close=can_close)

    @app.post("/loans/{loan_id}/contacts", dependencies=[Depends(check_csrf)])
    def contact_create(loan_id: int, request: Request, kind: str = Form("CALL"), outcome: str = Form("REACHED"),
                       contact_date: str = Form(""), note: str = Form(""), next_follow_up: str = Form(""),
                       promise_amount: str = Form(""), promise_date: str = Form(""),
                       username: str = Depends(user), s: Session = Depends(db)):
        t = today()
        act, promise = svc.log_contact(
            s, loan_id, actor=username, as_of=t, contact_date=parse_date(contact_date, t), kind=kind,
            outcome=outcome, note=note, next_follow_up=parse_date(next_follow_up, None) if next_follow_up else None,
            promise_amount_paise=svc.rupees_to_paise(promise_amount) if promise_amount else None,
            promise_date=parse_date(promise_date, None) if promise_date else None)
        log(request, s, "CONTACT_LOGGED", "collection_activity", act.id, loan_id=loan_id, type=kind,
            outcome=outcome, note=act.note)
        if promise:
            log(request, s, "PROMISE_MADE", "payment_promise", promise.id, loan_id=loan_id,
                amount_paise=promise.amount_paise, promised_date=promise.promised_date)
        msg = "Contact saved." + (f" Promise of {fmt.inr(promise.amount_paise)} by {fmt.dmy(promise.promised_date)} "
                                  "is tracked against real payments." if promise else "")
        return back(request, referer(request, f"/loans/{loan_id}"), flash=msg)

    @app.post("/promises/{promise_id}/cancel", dependencies=[Depends(check_csrf)])
    def promise_cancel(promise_id: int, request: Request, note: str = Form(""), _: str = Depends(user),
                       s: Session = Depends(db)):
        p = svc.cancel_promise(s, promise_id, note)
        log(request, s, "PROMISE_CANCELLED", "payment_promise", p.id, loan_id=p.loan_id,
            amount_paise=p.amount_paise, note=p.cancel_note)
        return back(request, referer(request, f"/loans/{p.loan_id}"), flash="Promise cancelled.")

    @app.get("/collections", response_class=HTMLResponse)
    def collections_page(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        ov = reports.collections_overview(s, today(), BOOKS[book_of(request)])
        return render(request, "collections.html", ov=ov)

    @app.post("/loans/{loan_id}/entries", dependencies=[Depends(check_csrf)])
    def loan_entry(loan_id: int, request: Request, type: str = Form(...), entry_date: str = Form(""),
                   amount: str = Form(""), new_rate: str = Form(""), payment_mode: str = Form("CASH"),
                   reference: str = Form(""), note: str = Form(""), _: str = Depends(user),
                   s: Session = Depends(db)):
        t = today()
        e, res = svc.post_entry(
            s, loan_id, type=type, entry_date=parse_date(entry_date, t), as_of=t,
            amount_paise=svc.rupees_to_paise(amount) if amount else None,
            new_rate=new_rate or None, payment_mode=payment_mode, reference=reference, note=note)
        log(request, s, "ENTRY_POSTED", "ledger_entry", e.id, loan_id=loan_id, type=e.type,
            amount_paise=e.amount_paise, entry_date=e.entry_date)
        msg = f"{type.title()} recorded."
        if type == "PAYMENT":
            split = ", ".join(f"{a.bucket.lower()} {fmt.inr(a.amount_paise)}"
                              for a in res.allocations if a.entry_id == e.id)
            msg = f"Payment of {fmt.inr(e.amount_paise)} recorded: {split or 'held as credit'}."
            rc = receipt_of(s, e.id)
            if rc:
                msg += f" Receipt {rc.number} issued."
        return back(request, referer(request, f"/loans/{loan_id}"), flash=msg)

    @app.post("/entries/{entry_id}/reverse", dependencies=[Depends(check_csrf)])
    def entry_reverse(entry_id: int, request: Request, note: str = Form(""), _: str = Depends(user),
                      s: Session = Depends(db)):
        t = today()
        original = s.get(m.LedgerEntry, entry_id)
        if original is None:
            raise svc.ServiceError(f"entry {entry_id} not found")
        loan_id = original.loan_id
        svc.reverse_entry(s, entry_id, entry_date=t, as_of=t, note=note)
        log(request, s, "ENTRY_REVERSED", "ledger_entry", entry_id, loan_id=loan_id, type=original.type,
            amount_paise=original.amount_paise, note=note or None)
        return back(request, f"/loans/{loan_id}", flash=f"Entry {entry_id} reversed.")

    @app.post("/loans/{loan_id}/status", dependencies=[Depends(check_csrf)])
    def loan_status(loan_id: int, request: Request, status: str = Form(""), _: str = Depends(user),
                    s: Session = Depends(db)):
        before = svc.get_loan(s, loan_id).status
        svc.set_owner_status(s, loan_id, status or None, today())
        log(request, s, "STATUS_CHANGED", "loan", loan_id, before=before, after=status or "ACTIVE")
        return back(request, f"/loans/{loan_id}", flash="Status updated.")

    @app.get("/quick", response_class=HTMLResponse)
    def quick_page(request: Request, loan_id: int | None = None, _: str = Depends(user),
                   s: Session = Depends(db)):
        rows = [r for r in svc.portfolio(s, today(), BOOKS[book_of(request)])
                if r.status not in ("CLOSED", "SETTLED", "WRITTEN_OFF")]
        return render(request, "quick.html", rows=rows, selected=loan_id)

    @app.post("/quick", dependencies=[Depends(check_csrf)])
    def quick_save(request: Request, loan_id: int = Form(...), amount: str = Form(...),
                   entry_date: str = Form(""), payment_mode: str = Form("CASH"), reference: str = Form(""),
                   _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        e, res = svc.post_entry(s, loan_id, type="PAYMENT", entry_date=parse_date(entry_date, t), as_of=t,
                                amount_paise=svc.rupees_to_paise(amount), payment_mode=payment_mode,
                                reference=reference)
        log(request, s, "ENTRY_POSTED", "ledger_entry", e.id, loan_id=loan_id, type=e.type,
            amount_paise=e.amount_paise, entry_date=e.entry_date, via="quick")
        split = ", ".join(f"{a.bucket.lower()} {fmt.inr(a.amount_paise)}"
                          for a in res.allocations if a.entry_id == e.id)
        rc = receipt_of(s, e.id)
        return back(request, "/quick", flash=f"Saved {fmt.inr(e.amount_paise)} on loan #{loan_id}: "
                                              f"{split or 'held as credit'}."
                                              + (f" Receipt {rc.number} issued." if rc else ""))

    @app.get("/funding", response_class=HTMLResponse)
    def funding_page(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        rows = svc.portfolio(s, today())
        by_id = {r.loan.id: r for r in rows}
        links = svc.funding_links(s)
        terms = {r.loan.id: svc.to_terms(r.loan) for r in rows}
        from loan_tracker import engine as eng
        rep = eng.spread([eng.FundingLink(l.taken_loan_id, l.given_loan_id, l.amount_paise, l.link_date)
                          for l in links], terms,
                         outstanding={r.loan.id: r.result.balances.principal_outstanding_paise for r in rows})
        return render(request, "funding.html", links=list(zip(links, rep.links)), by_id=by_id,
                      idle=rep.idle_cost_paise_per_cycle,
                      taken=[r for r in rows if r.loan.direction == "TAKEN"],
                      given=[r for r in rows if r.loan.direction == "GIVEN"])

    @app.post("/funding", dependencies=[Depends(check_csrf)])
    def funding_create(request: Request, taken_loan_id: int = Form(...), given_loan_id: int = Form(...),
                       amount: str = Form(...), link_date: str = Form(""),
                       confirm_cross_book: str = Form(""), _: str = Depends(user), s: Session = Depends(db)):
        link = svc.link_funding(s, taken_loan_id=taken_loan_id, given_loan_id=given_loan_id,
                                amount_paise=svc.rupees_to_paise(amount), link_date=parse_date(link_date, today()),
                                confirm_cross_book=bool(confirm_cross_book))
        log(request, s, "FUNDING_LINKED", "funding_link", link.id, taken_loan_id=taken_loan_id,
            given_loan_id=given_loan_id, amount_paise=link.amount_paise)
        return back(request, "/funding", flash="Funding link saved.")

    @app.get("/activity", response_class=HTMLResponse)
    def activity_page(request: Request, loan_id: int | None = None, _: str = Depends(user),
                      s: Session = Depends(db)):
        rows = svc.audit_log(s, limit=300, loan_id=loan_id)
        return render(request, "activity.html", rows=rows, loan_id=loan_id)

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar_page(request: Request, days: int = 30, _: str = Depends(user), s: Session = Depends(db)):
        days = days if days in (30, 60, 90) else 30
        t = today()
        cal = reports.cash_calendar(s, t, days, BOOKS[book_of(request)])
        tin = sum(i.amount_paise for d in cal for i in d.money_in)
        tout = sum(i.amount_paise for d in cal for i in d.money_out)
        low = min(cal, key=lambda d: d.running_paise, default=None)
        totals = {"in": tin, "out": tout, "net": tin - tout,
                  "low": min(0, low.running_paise) if low else 0,
                  "low_day": low.day if low and low.running_paise < 0 else None}
        weeks = reports.calendar_weeks(cal, t, t + timedelta(days=days))
        return render(request, "calendar.html", cal=cal, days=days, totals=totals, weeks=weeks)

    @app.get("/reports", response_class=HTMLResponse)
    def reports_page(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        return render(request, "reports.html", fy=reports.fy_interest_report(s, t),
                      overdue=reports.overdue_report(s, t, BOOKS[book_of(request)]),
                      cross=reports.cross_book_report(s, t))

    @app.get("/reports/statement/{loan_id}", response_class=HTMLResponse)
    def statement(loan_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        loan = svc.get_loan(s, loan_id)
        res = svc.compute_loan(loan, today())
        reversed_ids = {e.reverses_id for e in loan.entries if e.reverses_id}
        return render(request, "statement.html", loan=loan, r=res, reversed_ids=reversed_ids)

    @app.get("/receipts", response_class=HTMLResponse)
    def receipts_page(request: Request, q: str = "", _: str = Depends(user), s: Session = Depends(db)):
        return render(request, "receipts.html", receipts=svc.receipts(s, q=q), q=q,
                      missing=len(svc.payments_without_receipt(s)))

    @app.post("/receipts/backfill", dependencies=[Depends(check_csrf)])
    def receipts_backfill(request: Request, loan_id: str = Form(""), _: str = Depends(user),
                          s: Session = Depends(db)):
        made = svc.issue_missing_receipts(s, today(), int(loan_id) if loan_id.isdigit() else None)
        if made:
            log(request, s, "RECEIPTS_ISSUED", "receipt", made[0].id, count=len(made),
                first=made[0].number, last=made[-1].number)
        return back(request, referer(request, "/receipts"), flash=f"{len(made)} receipt(s) issued for older payments."
                    if made else "Every payment already has a receipt.")

    @app.get("/receipts/{receipt_id}", response_class=HTMLResponse)
    def receipt_page(receipt_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        from loan_tracker.collections import wa_link
        from loan_tracker.receipts import amount_in_words
        r = svc.get_receipt(s, receipt_id)
        text = (f"Receipt {r.number}: received {fmt.inr(r.amount_paise)} on {fmt.dmy(r.payment_date)} "
                f"for loan LN-{r.loan_id}"
                + (f" (interest {fmt.inr(r.interest_paise)}" + (f", principal {fmt.inr(r.principal_paise)}"
                                                               if r.principal_paise else "") + ")"
                   if r.interest_paise or r.principal_paise else "")
                + f". Principal outstanding {fmt.inr(r.principal_left_paise)}. Thank you.")
        return render(request, "receipt.html", r=r, words=amount_in_words(r.amount_paise),
                      issuer=issuer_for(r.loan.book), wa=None if r.cancelled_at else wa_link(r.loan.party.phone, text))

    @app.get("/loans/{loan_id}/closure", response_class=HTMLResponse)
    def closure_page(loan_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        c = reports.closure_summary(s, loan_id, today())
        return render(request, "closure.html", c=c, loan=c.loan, issuer=issuer_for(c.loan.book))

    @app.get("/export.csv")
    def export(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        body = reports.export_csv(s, t, BOOKS[book_of(request)])
        return PlainTextResponse(body, media_type="text/csv", headers={
            "Content-Disposition": f'attachment; filename="loan-tracker-{t.isoformat()}.csv"'})

    # ------------------------------------------------------------- JSON API
    def jsonable(obj):
        if is_dataclass(obj):
            return {k: jsonable(v) for k, v in asdict(obj).items()}
        if isinstance(obj, dict):
            return {str(k): jsonable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [jsonable(v) for v in obj]
        if isinstance(obj, (date, Decimal)):
            return str(obj)
        return obj

    def loan_json(loan: m.Loan, res) -> dict:
        nxt = res.projected_due.due_date if res.projected_due else None
        return {
            "id": loan.id, "party_id": loan.party_id, "party_name": loan.party.name,
            "direction": loan.direction, "book": loan.book, "status": loan.status,
            "principal_paise": loan.principal_paise, "start_date": str(loan.start_date),
            "rate": str(loan.rate), "rate_unit": loan.rate_unit, "interest_cycle": loan.interest_cycle,
            "due_day": loan.due_day, "grace_days": loan.grace_days,
            "maturity_date": str(loan.maturity_date) if loan.maturity_date else None,
            "principal_outstanding_paise": res.balances.principal_outstanding_paise,
            "payoff_today_paise": res.balances.payoff_today_paise,
            "next_due_date": str(nxt) if nxt else None,
        }

    @app.get("/api/dashboard")
    def api_dashboard(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        return jsonable(reports.dashboard(s, today(), BOOKS[book_of(request)],
                                          cash_buffer_paise=settings.cash_buffer_paise))

    @app.get("/api/parties")
    def api_parties(request: Request, q: str = "", _: str = Depends(user), s: Session = Depends(db)):
        return jsonable(reports_parties(s, today(), BOOKS[book_of(request)], q))

    @app.post("/api/parties", status_code=201)
    def api_party_create(request: Request, payload: PartyIn, _: str = Depends(user), s: Session = Depends(db)):
        p = svc.create_party(s, payload.name, **payload.model_dump(exclude={"name"}))
        log(request, s, "PARTY_CREATED", "party", p.id, name=p.name, via="api")
        return {"id": p.id, "name": p.name}

    @app.get("/api/loans")
    def api_loans(request: Request, direction: str = "", status: str = "", party_id: int | None = None,
                  _: str = Depends(user), s: Session = Depends(db)):
        rows = svc.portfolio(s, today(), BOOKS[book_of(request)], direction=direction or None,
                             party_id=party_id)
        return [loan_json(r.loan, r.result) | {"status": r.status} for r in rows if not status or r.status == status]

    @app.get("/api/loans/{loan_id}")
    def api_loan(loan_id: int, as_of: str = "", _: str = Depends(user), s: Session = Depends(db)):
        loan = svc.get_loan(s, loan_id)
        res = svc.compute_loan(loan, parse_date(as_of, today()))
        out = loan_json(loan, res)
        out.update(balances=jsonable(res.balances), dues=jsonable(res.dues),
                   projected_due=jsonable(res.projected_due), allocations=jsonable(res.allocations),
                   engine_status=res.loan_status)
        return out

    @app.post("/api/loans", status_code=201)
    def api_loan_create(request: Request, payload: LoanIn, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        fields = payload.model_dump(exclude={"start_date", "rate"})
        loan = svc.create_loan(s, start_date=payload.start_date or t, rate=svc.parse_rate(payload.rate),
                               as_of=t, **fields)
        log(request, s, "LOAN_CREATED", "loan", loan.id, direction=loan.direction, book=loan.book,
            principal_paise=loan.principal_paise, rate=loan.rate, via="api")
        return {"id": loan.id}

    @app.get("/api/loans/{loan_id}/entries")
    def api_entries(loan_id: int, _: str = Depends(user), s: Session = Depends(db)):
        loan = svc.get_loan(s, loan_id)
        return [{"id": e.id, "entry_date": str(e.entry_date), "type": e.type, "amount_paise": e.amount_paise,
                 "payment_mode": e.payment_mode, "reference": e.reference, "note": e.note,
                 "new_rate": str(e.new_rate) if e.new_rate is not None else None,
                 "reverses_id": e.reverses_id} for e in reversed(loan.entries)]

    @app.post("/api/loans/{loan_id}/entries", status_code=201)
    def api_entry_create(request: Request, loan_id: int, payload: EntryIn, _: str = Depends(user),
                         s: Session = Depends(db)):
        t = today()
        e, res = svc.post_entry(s, loan_id, entry_date=payload.entry_date or t, as_of=t,
                                **payload.model_dump(exclude={"entry_date"}))
        log(request, s, "ENTRY_POSTED", "ledger_entry", e.id, loan_id=loan_id, type=e.type,
            amount_paise=e.amount_paise, entry_date=e.entry_date, via="api")
        return {"id": e.id, "allocations": jsonable([a for a in res.allocations if a.entry_id == e.id]),
                "balances": jsonable(res.balances)}

    @app.post("/api/entries/{entry_id}/reverse", status_code=201)
    def api_reverse(request: Request, entry_id: int, payload: ReverseIn | None = None, _: str = Depends(user),
                    s: Session = Depends(db)):
        t = today()
        note = payload.note if payload else None
        res = svc.reverse_entry(s, entry_id, entry_date=t, as_of=t, note=note)
        original = s.get(m.LedgerEntry, entry_id)
        log(request, s, "ENTRY_REVERSED", "ledger_entry", entry_id, loan_id=original.loan_id,
            type=original.type, amount_paise=original.amount_paise, note=note, via="api")
        return {"balances": jsonable(res.balances)}

    @app.post("/api/funding", status_code=201)
    def api_funding(request: Request, payload: FundingIn, _: str = Depends(user), s: Session = Depends(db)):
        link = svc.link_funding(s, link_date=payload.link_date or today(),
                                **payload.model_dump(exclude={"link_date"}))
        log(request, s, "FUNDING_LINKED", "funding_link", link.id, taken_loan_id=link.taken_loan_id,
            given_loan_id=link.given_loan_id, amount_paise=link.amount_paise, via="api")
        return {"id": link.id}

    @app.get("/api/openapi.json", include_in_schema=False)
    def api_schema(_: str = Depends(user)):
        from fastapi.openapi.utils import get_openapi
        return get_openapi(title=app.title, version="1", routes=app.routes)

    @app.get("/api/docs", include_in_schema=False)
    def api_docs(_: str = Depends(user)):
        from fastapi.openapi.docs import get_swagger_ui_html
        return get_swagger_ui_html(openapi_url="/api/openapi.json", title="Loan Tracker API")

    @app.get("/api/receipts")
    def api_receipts(loan_id: int | None = None, q: str = "", _: str = Depends(user), s: Session = Depends(db)):
        return [receipt_json(r) for r in svc.receipts(s, loan_id=loan_id, q=q)]

    @app.get("/api/receipts/{receipt_id}")
    def api_receipt(receipt_id: int, _: str = Depends(user), s: Session = Depends(db)):
        from loan_tracker.receipts import amount_in_words
        r = svc.get_receipt(s, receipt_id)
        return receipt_json(r) | {"amount_in_words": amount_in_words(r.amount_paise)}

    @app.get("/api/collections")
    def api_collections(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        return jsonable(reports.collections_overview(s, today(), BOOKS[book_of(request)]))

    @app.get("/api/loans/{loan_id}/contacts")
    def api_contacts(loan_id: int, _: str = Depends(user), s: Session = Depends(db)):
        loan = svc.get_loan(s, loan_id)
        acts, proms = svc.loan_collections(s, loan_id)
        return {"contacts": [{"id": a.id, "contact_date": str(a.contact_date), "kind": a.kind, "outcome": a.outcome,
                              "note": a.note, "next_follow_up": str(a.next_follow_up) if a.next_follow_up else None,
                              "by": a.actor} for a in acts],
                "promises": [{"id": p.id, "made_on": str(p.made_on), "promised_date": str(p.promised_date),
                              "amount_paise": p.amount_paise} | jsonable(svc.promise_state(p, loan, today()))
                             for p in proms]}

    @app.post("/api/loans/{loan_id}/contacts", status_code=201)
    def api_contact_create(request: Request, loan_id: int, payload: ContactIn, username: str = Depends(user),
                           s: Session = Depends(db)):
        t = today()
        act, promise = svc.log_contact(s, loan_id, actor=username, as_of=t,
                                       **payload.model_dump(exclude_none=True))
        log(request, s, "CONTACT_LOGGED", "collection_activity", act.id, loan_id=loan_id, type=act.kind,
            outcome=act.outcome, note=act.note, via="api")
        if promise:
            log(request, s, "PROMISE_MADE", "payment_promise", promise.id, loan_id=loan_id,
                amount_paise=promise.amount_paise, promised_date=promise.promised_date, via="api")
        return {"id": act.id, "promise_id": promise.id if promise else None}

    @app.post("/api/promises/{promise_id}/cancel")
    def api_promise_cancel(request: Request, promise_id: int, payload: ReverseIn | None = None,
                           _: str = Depends(user), s: Session = Depends(db)):
        p = svc.cancel_promise(s, promise_id, payload.note if payload else None)
        log(request, s, "PROMISE_CANCELLED", "payment_promise", p.id, loan_id=p.loan_id,
            amount_paise=p.amount_paise, note=p.cancel_note, via="api")
        return {"id": p.id, "cancelled": True}

    @app.get("/api/calendar")
    def api_calendar(request: Request, days: int = 30, _: str = Depends(user), s: Session = Depends(db)):
        return jsonable(reports.cash_calendar(s, today(), days, BOOKS[book_of(request)]))

    @app.get("/api/reports/fy-interest")
    def api_fy(_: str = Depends(user), s: Session = Depends(db)):
        return reports.fy_interest_report(s, today())

    @app.get("/api/reports/overdue")
    def api_overdue(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        return jsonable(reports.overdue_report(s, today(), BOOKS[book_of(request)]))

    @app.get("/api/reminders")
    def api_reminders(_: str = Depends(user), s: Session = Depends(db)):
        from loan_tracker.jobs.daily import build_digest
        return jsonable(build_digest(s, today(), settings))

    return app


# ---------------------------------------------------------------- helpers
def party_summary(rows: list[svc.LoanRow]) -> dict:
    lent = sum(r.result.balances.principal_outstanding_paise for r in rows if r.loan.direction == "GIVEN")
    taken = sum(r.result.balances.principal_outstanding_paise for r in rows if r.loan.direction == "TAKEN")
    dues = [d for r in rows if r.loan.direction == "GIVEN" for d in r.result.dues]
    settled = [d for d in dues if d.status in ("PAID", "WAIVED")]
    on_time = [d for d in settled if d.status == "WAIVED" or _paid_on_time(rows, d)]
    return {"lent": lent, "taken": taken, "net": lent - taken,
            "on_time_pct": (len(on_time) * 100 // len(settled)) if settled else None,
            "dues": len(dues)}


def _paid_on_time(rows, due_status) -> bool:
    for r in rows:
        if r.loan.id != due_status.due.loan_id:
            continue
        dates = {e.id: e.entry_date for e in r.loan.entries}
        last = max((dates[a.entry_id] for a in r.result.allocations
                    if a.bucket == "INTEREST" and a.due_period_start == due_status.due.period_start),
                   default=None)
        return last is not None and last <= due_status.due.due_date + timedelta(days=r.loan.grace_days)
    return False


def reports_parties(s: Session, as_of: date, book: str | None, q: str = "") -> list[dict]:
    query = select(m.Party).order_by(m.Party.name)
    if q:
        query = query.where(func.lower(m.Party.name).contains(q.lower()))
    rows = svc.portfolio(s, as_of, book)
    by_party: dict[int, list] = defaultdict(list)
    for r in rows:
        by_party[r.loan.party_id].append(r)
    out = []
    for p in s.scalars(query):
        summary = party_summary(by_party.get(p.id, []))
        out.append({"id": p.id, "name": p.name, "phone": p.phone, "loan_count": len(by_party.get(p.id, [])),
                    "net_position_paise": summary["net"], "lent_paise": summary["lent"],
                    "taken_paise": summary["taken"]})
    return out


app = create_app()
