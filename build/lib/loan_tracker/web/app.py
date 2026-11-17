"""FastAPI application: phone-first HTML pages + JSON API (docs/design/api.md).

Run:  uvicorn loan_tracker.web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import asdict, is_dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterator

from fastapi import Depends, FastAPI, Form, HTTPException, Request
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

HERE = Path(__file__).resolve().parent
BOOKS = {"all": None, "business": "BUSINESS", "personal": "PERSONAL"}
PUBLIC_PATHS = ("/login", "/static", "/healthz", "/manifest.webmanifest", "/sw.js")


class LoginRequired(Exception):
    pass


def create_app(settings: Settings | None = None, *, maker: sessionmaker | None = None,
               today_fn: Callable[[], date] | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="Loan Tracker", docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.maker = maker or session_factory(settings.database_url)
    app.state.today = today_fn or (lambda: clock_today(settings))
    app.state.failed_logins = defaultdict(deque)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                       https_only=settings.secure_cookies, max_age=12 * 3600)
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=HERE / "templates")
    templates.env.filters.update(inr=fmt.inr, dmy=fmt.dmy, dshort=fmt.dshort, pct=fmt.pct, rupees_input=fmt.rupees_input)

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

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        ctx.update(request=request, book=book_of(request), today=app.state.today(),
                   flash=request.session.pop("flash", None), error=request.session.pop("error", None),
                   user=request.session.get("user"))
        return templates.TemplateResponse(request, name, ctx)

    def back(request: Request, url: str, *, flash: str | None = None, error: str | None = None):
        if flash:
            request.session["flash"] = flash
        if error:
            request.session["error"] = error
        return RedirectResponse(url, status_code=303)

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

    @app.exception_handler(svc.ServiceError)
    async def _service_error(request: Request, exc: svc.ServiceError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": str(exc)}, status_code=400)
        request.session["error"] = str(exc)
        return RedirectResponse(request.headers.get("referer") or "/", status_code=303)

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

    @app.post("/login")
    def login(request: Request, username: str = Form(...), password: str = Form(...),
              code: str = Form(""), s: Session = Depends(db)):
        ip = request.client.host if request.client else "?"
        fails = app.state.failed_logins[ip]
        now = time.time()
        while fails and now - fails[0] > 900:
            fails.popleft()
        if len(fails) >= 5:
            return back(request, "/login", error="Too many attempts. Wait 15 minutes.")
        u = s.scalar(select(m.AppUser).where(m.AppUser.username == username.strip()))
        ok = u is not None and auth.verify_password(password, u.password_hash)
        if ok and u.totp_secret:
            ok = auth.verify_totp(u.totp_secret, code)
        if not ok:
            fails.append(now)
            return back(request, "/login", error="Wrong username, password or code.")
        fails.clear()
        request.session.clear()
        request.session["user"] = u.username
        return RedirectResponse("/", status_code=303)

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    # ------------------------------------------------------------- pages
    @app.get("/book/{name}")
    def set_book(name: str, request: Request, _: str = Depends(user)):
        resp = RedirectResponse(request.headers.get("referer") or "/", status_code=303)
        resp.set_cookie("book", name if name in BOOKS else "all", max_age=365 * 86400, samesite="lax")
        return resp

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        dash = reports.dashboard(s, today(), BOOKS[book_of(request)],
                                 concentration_flag_pct=settings.concentration_flag_pct)
        last = s.scalar(select(m.JobRun).order_by(m.JobRun.started_at.desc()).limit(1))
        return render(request, "home.html", d=dash, last_job=last)

    @app.get("/parties", response_class=HTMLResponse)
    def parties_page(request: Request, q: str = "", _: str = Depends(user), s: Session = Depends(db)):
        rows = reports_parties(s, today(), BOOKS[book_of(request)], q)
        return render(request, "parties.html", rows=rows, q=q)

    @app.post("/parties")
    def parties_create(request: Request, name: str = Form(...), phone: str = Form(""),
                       address: str = Form(""), notes: str = Form(""), _: str = Depends(user),
                       s: Session = Depends(db)):
        p = svc.create_party(s, name, phone=phone, address=address, notes=notes)
        return back(request, f"/parties/{p.id}", flash=f"Added {p.name}.")

    @app.get("/parties/{party_id}", response_class=HTMLResponse)
    def party_page(party_id: int, request: Request, _: str = Depends(user), s: Session = Depends(db)):
        party = svc.get_party(s, party_id)
        rows = svc.portfolio(s, today(), BOOKS[book_of(request)], party_id=party_id)
        return render(request, "party.html", party=party, rows=rows, summary=party_summary(rows))

    @app.post("/parties/{party_id}")
    def party_update(party_id: int, request: Request, name: str = Form(...), phone: str = Form(""),
                     address: str = Form(""), id_proof_ref: str = Form(""), guarantor: str = Form(""),
                     notes: str = Form(""), risk_tag: str = Form(""), _: str = Depends(user),
                     s: Session = Depends(db)):
        svc.update_party(s, party_id, name=name, phone=phone, address=address, id_proof_ref=id_proof_ref,
                         guarantor=guarantor, notes=notes, risk_tag=risk_tag)
        return back(request, f"/parties/{party_id}", flash="Saved.")

    @app.get("/loans", response_class=HTMLResponse)
    def loans_page(request: Request, direction: str = "", status: str = "", _: str = Depends(user),
                   s: Session = Depends(db)):
        rows = svc.portfolio(s, today(), BOOKS[book_of(request)], direction=direction or None)
        if status:
            rows = [r for r in rows if r.loan.status == status]
        return render(request, "loans.html", rows=rows, direction=direction, status=status)

    @app.get("/loans/new", response_class=HTMLResponse)
    def loan_new(request: Request, party_id: int | None = None, direction: str = "GIVEN",
                 _: str = Depends(user), s: Session = Depends(db)):
        parties = s.scalars(select(m.Party).order_by(m.Party.name)).all()
        return render(request, "loan_new.html", parties=parties, party_id=party_id, direction=direction)

    @app.post("/loans")
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
        return render(request, "loan.html", loan=loan, r=res, reversed_ids=reversed_ids, allocs=allocs,
                      links=links, others=others)

    @app.post("/loans/{loan_id}/entries")
    def loan_entry(loan_id: int, request: Request, type: str = Form(...), entry_date: str = Form(""),
                   amount: str = Form(""), new_rate: str = Form(""), payment_mode: str = Form("CASH"),
                   reference: str = Form(""), note: str = Form(""), _: str = Depends(user),
                   s: Session = Depends(db)):
        t = today()
        e, res = svc.post_entry(
            s, loan_id, type=type, entry_date=parse_date(entry_date, t), as_of=t,
            amount_paise=svc.rupees_to_paise(amount) if amount else None,
            new_rate=new_rate or None, payment_mode=payment_mode, reference=reference, note=note)
        msg = f"{type.title()} recorded."
        if type == "PAYMENT":
            split = ", ".join(f"{a.bucket.lower()} {fmt.inr(a.amount_paise)}"
                              for a in res.allocations if a.entry_id == e.id)
            msg = f"Payment of {fmt.inr(e.amount_paise)} recorded: {split or 'held as credit'}."
        return back(request, request.headers.get("referer") or f"/loans/{loan_id}", flash=msg)

    @app.post("/entries/{entry_id}/reverse")
    def entry_reverse(entry_id: int, request: Request, note: str = Form(""), _: str = Depends(user),
                      s: Session = Depends(db)):
        t = today()
        original = s.get(m.LedgerEntry, entry_id)
        if original is None:
            raise svc.ServiceError(f"entry {entry_id} not found")
        loan_id = original.loan_id
        svc.reverse_entry(s, entry_id, entry_date=t, as_of=t, note=note)
        return back(request, f"/loans/{loan_id}", flash=f"Entry {entry_id} reversed.")

    @app.post("/loans/{loan_id}/status")
    def loan_status(loan_id: int, request: Request, status: str = Form(""), _: str = Depends(user),
                    s: Session = Depends(db)):
        svc.set_owner_status(s, loan_id, status or None, today())
        return back(request, f"/loans/{loan_id}", flash="Status updated.")

    @app.get("/quick", response_class=HTMLResponse)
    def quick_page(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        rows = [r for r in svc.portfolio(s, today(), BOOKS[book_of(request)])
                if r.loan.status not in ("CLOSED", "SETTLED", "WRITTEN_OFF")]
        return render(request, "quick.html", rows=rows)

    @app.post("/quick")
    def quick_save(request: Request, loan_id: int = Form(...), amount: str = Form(...),
                   entry_date: str = Form(""), payment_mode: str = Form("CASH"), reference: str = Form(""),
                   _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        e, res = svc.post_entry(s, loan_id, type="PAYMENT", entry_date=parse_date(entry_date, t), as_of=t,
                                amount_paise=svc.rupees_to_paise(amount), payment_mode=payment_mode,
                                reference=reference)
        split = ", ".join(f"{a.bucket.lower()} {fmt.inr(a.amount_paise)}"
                          for a in res.allocations if a.entry_id == e.id)
        return back(request, "/quick", flash=f"Saved {fmt.inr(e.amount_paise)} on loan #{loan_id}: "
                                              f"{split or 'held as credit'}.")

    @app.get("/funding", response_class=HTMLResponse)
    def funding_page(request: Request, _: str = Depends(user), s: Session = Depends(db)):
        rows = svc.portfolio(s, today())
        by_id = {r.loan.id: r for r in rows}
        links = svc.funding_links(s)
        terms = {r.loan.id: svc.to_terms(r.loan) for r in rows}
        from loan_tracker import engine as eng
        rep = eng.spread([eng.FundingLink(l.taken_loan_id, l.given_loan_id, l.amount_paise, l.link_date)
                          for l in links], terms,
                         {r.loan.id: r.result.balances.principal_outstanding_paise
                          for r in rows if r.loan.direction == "TAKEN"})
        return render(request, "funding.html", links=list(zip(links, rep.links)), by_id=by_id,
                      idle=rep.idle_cost_paise_per_cycle,
                      taken=[r for r in rows if r.loan.direction == "TAKEN"],
                      given=[r for r in rows if r.loan.direction == "GIVEN"])

    @app.post("/funding")
    def funding_create(request: Request, taken_loan_id: int = Form(...), given_loan_id: int = Form(...),
                       amount: str = Form(...), link_date: str = Form(""),
                       confirm_cross_book: str = Form(""), _: str = Depends(user), s: Session = Depends(db)):
        svc.link_funding(s, taken_loan_id=taken_loan_id, given_loan_id=given_loan_id,
                         amount_paise=svc.rupees_to_paise(amount), link_date=parse_date(link_date, today()),
                         confirm_cross_book=bool(confirm_cross_book))
        return back(request, "/funding", flash="Funding link saved.")

    @app.get("/calendar", response_class=HTMLResponse)
    def calendar_page(request: Request, days: int = 30, _: str = Depends(user), s: Session = Depends(db)):
        days = days if days in (30, 60, 90) else 30
        cal = reports.cash_calendar(s, today(), days, BOOKS[book_of(request)])
        return render(request, "calendar.html", cal=cal, days=days)

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
        return jsonable(reports.dashboard(s, today(), BOOKS[book_of(request)]))

    @app.get("/api/parties")
    def api_parties(request: Request, q: str = "", _: str = Depends(user), s: Session = Depends(db)):
        return jsonable(reports_parties(s, today(), BOOKS[book_of(request)], q))

    @app.post("/api/parties", status_code=201)
    def api_party_create(payload: dict, _: str = Depends(user), s: Session = Depends(db)):
        p = svc.create_party(s, payload.get("name", ""), **{k: v for k, v in payload.items() if k != "name"})
        return {"id": p.id, "name": p.name}

    @app.get("/api/loans")
    def api_loans(request: Request, direction: str = "", status: str = "", party_id: int | None = None,
                  _: str = Depends(user), s: Session = Depends(db)):
        rows = svc.portfolio(s, today(), BOOKS[book_of(request)], direction=direction or None,
                             party_id=party_id)
        return [loan_json(r.loan, r.result) for r in rows if not status or r.loan.status == status]

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
    def api_loan_create(payload: dict, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        loan = svc.create_loan(
            s, party_id=int(payload["party_id"]), direction=payload.get("direction", ""),
            book=payload.get("book", ""), principal_paise=int(payload["principal_paise"]),
            start_date=parse_date(payload.get("start_date"), t), rate=svc.parse_rate(payload["rate"]),
            rate_unit=payload.get("rate_unit", "PER_MONTH"),
            interest_cycle=payload.get("interest_cycle", "MONTHLY"), due_day=payload.get("due_day"),
            grace_days=int(payload.get("grace_days", 3)),
            maturity_date=parse_date(payload.get("maturity_date"), None) if payload.get("maturity_date") else None,
            purpose=payload.get("purpose"), collateral=payload.get("collateral"), as_of=t)
        return {"id": loan.id}

    @app.get("/api/loans/{loan_id}/entries")
    def api_entries(loan_id: int, _: str = Depends(user), s: Session = Depends(db)):
        loan = svc.get_loan(s, loan_id)
        return [{"id": e.id, "entry_date": str(e.entry_date), "type": e.type, "amount_paise": e.amount_paise,
                 "payment_mode": e.payment_mode, "reference": e.reference, "note": e.note,
                 "new_rate": str(e.new_rate) if e.new_rate is not None else None,
                 "reverses_id": e.reverses_id} for e in reversed(loan.entries)]

    @app.post("/api/loans/{loan_id}/entries", status_code=201)
    def api_entry_create(loan_id: int, payload: dict, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        e, res = svc.post_entry(
            s, loan_id, type=payload.get("type", ""), entry_date=parse_date(payload.get("entry_date"), t),
            as_of=t, amount_paise=payload.get("amount_paise"), new_rate=payload.get("new_rate"),
            payment_mode=payload.get("payment_mode", "CASH"), reference=payload.get("reference"),
            note=payload.get("note"))
        return {"id": e.id, "allocations": jsonable([a for a in res.allocations if a.entry_id == e.id]),
                "balances": jsonable(res.balances)}

    @app.post("/api/entries/{entry_id}/reverse", status_code=201)
    def api_reverse(entry_id: int, payload: dict | None = None, _: str = Depends(user),
                    s: Session = Depends(db)):
        t = today()
        res = svc.reverse_entry(s, entry_id, entry_date=t, as_of=t, note=(payload or {}).get("note"))
        return {"balances": jsonable(res.balances)}

    @app.post("/api/funding", status_code=201)
    def api_funding(payload: dict, _: str = Depends(user), s: Session = Depends(db)):
        link = svc.link_funding(
            s, taken_loan_id=int(payload["taken_loan_id"]), given_loan_id=int(payload["given_loan_id"]),
            amount_paise=int(payload["amount_paise"]), link_date=parse_date(payload.get("link_date"), today()),
            confirm_cross_book=bool(payload.get("confirm_cross_book")))
        return {"id": link.id}

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
        from datetime import timedelta
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
