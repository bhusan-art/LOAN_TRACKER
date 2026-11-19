"""FastAPI application: phone-first HTML pages + JSON API (docs/design/api.md).

Run:  uvicorn loan_tracker.web.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import secrets
from collections import defaultdict, deque
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Iterator

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from loan_tracker import reports, services as svc
from loan_tracker.config import Settings, get_settings, today as clock_today
from loan_tracker.db import models as m
from loan_tracker.db.session import session_factory
from loan_tracker.engine.calendar import _add_months as add_months
from loan_tracker.web.schemas import (CashAccountIn, CashBalanceIn, ContactIn, EntryIn, FundingIn, LoanIn,
                                      PartyIn, PlannedIn, ReverseIn, SimulateLoanIn, SimulateNewIn,
                                      BankLinkIn, BankRecordIn, describe)

HERE = Path(__file__).resolve().parent
BOOKS = {"all": None, "business": "BUSINESS", "personal": "PERSONAL"}
PUBLIC_PATHS = ("/login", "/static", "/healthz", "/manifest.webmanifest", "/sw.js")


# POSTs a viewer may make: the what-if simulator only calculates and saves nothing, and
# signing your own phone out changes nothing of the owner's
VIEWER_POSTS = frozenset({"/api/ui/simulate", "/api/simulate/loan", "/api/simulate/new-loan", "/api/device/logout"})


class ReadOnly(Exception):
    """A viewer tried to change something."""


class LoginRequired(Exception):
    pass


class CsrfFailed(Exception):
    pass


ENTRY_WORDS = {"PAYMENT": "Payment", "DISBURSE": "Disbursement", "TOPUP": "Top-up", "RATE_CHANGE": "Rate change",
               "PENALTY": "Penalty", "WAIVER": "Waiver", "CHARGE": "Charge", "ADJUSTMENT": "Adjustment"}


def create_app(settings: Settings | None = None, *, maker: sessionmaker | None = None,
               today_fn: Callable[[], date] | None = None) -> FastAPI:
    settings = settings or get_settings()
    # API docs are served below behind the login, not publicly
    app = FastAPI(title="Loan Tracker", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.maker = maker or session_factory(settings.database_url)
    app.state.today = today_fn or (lambda: clock_today(settings))
    app.state.failed_logins = defaultdict(deque)
    app.state.failed_shares = defaultdict(deque)

    def recent_failures(table: dict, ip: str, now: float, window: int = 900) -> deque:
        """Failures from ip in the last window seconds. Old entries and empty addresses
        are dropped, so many one-off addresses cannot grow memory without bound."""
        for key in [k for k, q in table.items() if not q or now - q[-1] > window]:
            del table[key]
        fails = table[ip]
        while fails and now - fails[0] > window:
            fails.popleft()
        return fails
    vault = svc.DocVault.from_settings(settings)
    # the Android app's screens run at https://localhost and call this server: allow that
    # origin only, without cookies (the app uses a bearer token)
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.app_origins), allow_credentials=False,
                       allow_methods=["GET", "POST"], allow_headers=["Authorization", "Content-Type", "X-Book"],
                       expose_headers=["Content-Disposition"], max_age=600)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, same_site="lax",
                       https_only=settings.secure_cookies, max_age=12 * 3600)
    async def api_csrf(request: Request) -> None:
        """CSRF guard for the API: every request that changes something must send the
        session's token in the X-CSRF-Token header (from GET /api/session). A cross-site
        page cannot read the token or set the header, for JSON and file uploads alike."""
        if request.method in ("GET", "HEAD", "OPTIONS") or not request.url.path.startswith("/api/"):
            return
        # the Android app: a bearer token is not sent by the browser on its own, and other
        # sites may not send this header (CORS allows only the app). Phone sign-in uses no cookie.
        if request.headers.get("authorization", "").lower().startswith("bearer ") or request.url.path == "/api/device/login":
            return
        sent = request.headers.get("x-csrf-token", "")
        if not sent or not secrets.compare_digest(sent, request.session.get("csrf", "")):
            raise CsrfFailed()

    app.router.dependencies.append(Depends(api_csrf))

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
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
        svc.audit(s, actor or getattr(request.state, "user", None) or request.session.get("user") or "unknown",
                  action, entity, entity_id,
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

    def who(request: Request, s: Session) -> m.AppUser | None:
        """The signed-in user: a phone's bearer token (Android app) or the browser's session
        cookie, checked against the database on every request (a disabled user, a password
        reset or a signed-out phone ends access at once). A request that sends a bearer
        token is judged by the token alone, never by a cookie."""
        auth_header = request.headers.get("authorization", "")
        if auth_header:
            scheme, _, token = auth_header.partition(" ")
            d = svc.resolve_device(s, token.strip(), datetime.now(timezone.utc)) if scheme.lower() == "bearer" else None
            if d is None:
                return None
            request.state.user, request.state.device_id = d.user.username, d.id
            return d.user
        name = request.session.get("user")
        if not name:
            return None
        u = s.scalar(select(m.AppUser).where(m.AppUser.username == name))
        if u is None or u.disabled_at is not None or request.session.get("epoch") != u.session_epoch:
            request.session.clear()
            return None
        request.state.user, request.state.device_id = u.username, None
        return u

    def user(request: Request, s: Session = Depends(db)) -> str:
        """The logged-in user's name. Viewers may only read: every request other than
        GET/HEAD from a viewer is refused here, for every route, so a new route cannot
        forget it (except VIEWER_POSTS, which change nothing of the owner's)."""
        u = who(request, s)
        if u is None:
            raise LoginRequired()
        request.state.role = u.role
        if u.role != "OWNER" and request.method not in ("GET", "HEAD") and request.url.path not in VIEWER_POSTS:
            raise ReadOnly()
        return u.username

    def owner_only(request: Request, name: str = Depends(user)) -> str:
        """For things viewers may not even look at (documents: ID proofs, agreements)."""
        if request.state.role != "OWNER":
            raise ReadOnly()
        return name

    def today() -> date:
        return app.state.today()

    def book_of(request: Request) -> str:
        # ?book=, then the app's X-Book header (the Android app sends no cookies), then the cookie
        q = (request.query_params.get("book") or request.headers.get("x-book") or request.cookies.get("book") or "all").lower()
        return q if q in BOOKS else "all"

    def csrf_token(request: Request) -> str:
        tok = request.session.get("csrf")
        if not tok:
            tok = request.session["csrf"] = secrets.token_urlsafe(24)
        return tok

    def parse_date(v: str | None, default: date) -> date:
        if not v:
            return default
        try:
            return date.fromisoformat(v)
        except ValueError as exc:
            raise svc.ServiceError(f"not a date: {v!r}") from exc

    @app.exception_handler(ReadOnly)
    async def _read_only(request: Request, exc: ReadOnly):
        msg = "Read-only access: you can look, but only the owner can make changes or open documents."
        return JSONResponse({"detail": msg}, status_code=403)

    @app.exception_handler(CsrfFailed)
    async def _csrf_failed(request: Request, exc: CsrfFailed):
        return JSONResponse({"detail": "This page expired. Reload and try again."}, status_code=403)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        return JSONResponse({"detail": "login required"}, status_code=401)

    @app.exception_handler(RequestValidationError)
    async def _bad_request(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        return JSONResponse({"detail": describe(errors),
                             "errors": [{"field": ".".join(str(x) for x in e.get("loc", ()) if x != "body"),
                                         "message": e.get("msg")} for e in errors]}, status_code=400)

    @app.exception_handler(svc.ServiceError)
    async def _service_error(request: Request, exc: svc.ServiceError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    # ------------------------------------------------------------- public
    @app.get("/healthz")
    def healthz(s: Session = Depends(db)):
        last = s.scalar(select(m.JobRun).order_by(m.JobRun.started_at.desc()).limit(1))
        return {"ok": True, "last_job_run": last.run_date.isoformat() if last else None,
                "last_job_ok": last.ok if last else None}

    @app.get("/manifest.webmanifest")
    def manifest():
        return JSONResponse({
            "name": "Ledgerly", "short_name": "Ledgerly", "start_url": "/", "display": "standalone",
            "background_color": "#f4f8f7", "theme_color": "#082f35",
            "icons": [{"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }, media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        return Response((HERE / "static" / "sw.js").read_text(), media_type="application/javascript")

    # ---- borrower statement links (Phase 15) -------------------------------------------
    PUBLIC_HEADERS = {"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex, nofollow",
                      "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
                      "X-Frame-Options": "DENY",
                      "Content-Security-Policy": "default-src 'none'; style-src 'self'; img-src 'self' data:; "
                                                 "form-action 'none'; frame-ancestors 'none'; base-uri 'none'"}

    @app.get("/api/documents/{doc_id}/file")
    def document_file(doc_id: int, request: Request, download: int = 0, _: str = Depends(owner_only),
                      s: Session = Depends(db)):
        from urllib.parse import quote
        d, data = svc.read_document(s, vault, doc_id)
        log(request, s, "DOC_VIEWED", "document", d.id, name=d.title, loan_id=d.loan_id)
        disposition = "attachment" if download else "inline"
        headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(d.file_name)}",
                   "Cache-Control": "no-store, private", "X-Content-Type-Options": "nosniff",
                   "Referrer-Policy": "no-referrer",
                   # images open in a locked-down sandbox; the browser's own PDF viewer needs plugins
                   "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
                   + ("; sandbox" if d.content_type.startswith("image/") else "; object-src 'self'")}
        return Response(content=data, media_type=d.content_type, headers=headers)

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

    def forecast_json(f) -> dict:
        def one(x):
            return {"short_date": str(x.short_date) if x.short_date else None, "short_paise": x.short_paise,
                    "low_paise": x.low_paise, "low_date": str(x.low_date) if x.low_date else None,
                    "days": [{"day": str(d.day), "in_paise": d.in_paise, "out_paise": d.out_paise,
                              "running_paise": d.running_paise} for d in x.days]}
        return {"as_of": str(f.as_of), "days": f.days, "opening_paise": f.opening_paise,
                "balances_as_of": str(f.balances_as_of) if f.balances_as_of else None,
                "stale_days": f.stale_days, "buffer_paise": f.buffer_paise, "risky_in_paise": f.risky_in_paise,
                "accounts": [{"id": a.id, "name": a.name, "kind": a.kind, "book": a.book,
                              "balance_paise": b.balance_paise if b else None,
                              "as_of": str(b.as_of) if b else None} for a, b in f.accounts],
                "expected": one(f.expected), "cautious": one(f.cautious), "warnings": reports.cash_warnings(f)}

    @app.get("/api/cash")
    def api_cash(request: Request, days: int = 30, _: str = Depends(user), s: Session = Depends(db)):
        days = max(1, min(days, 365))
        return forecast_json(reports.cash_forecast(s, today(), days, BOOKS[book_of(request)],
                                                   settings.cash_buffer_paise))

    @app.post("/api/cash/accounts", status_code=201)
    def api_cash_account(request: Request, payload: CashAccountIn, _: str = Depends(user),
                         s: Session = Depends(db)):
        acc = svc.create_cash_account(s, as_of=today(), **payload.model_dump())
        log(request, s, "CASH_ACCOUNT_ADDED", "cash_account", acc.id, name=acc.name, book=acc.book,
            amount_paise=payload.balance_paise, via="api")
        return {"id": acc.id}

    @app.post("/api/cash/accounts/{account_id}/balance", status_code=201)
    def api_cash_balance(request: Request, account_id: int, payload: CashBalanceIn, _: str = Depends(user),
                         s: Session = Depends(db)):
        t = today()
        b = svc.set_cash_balance(s, account_id, balance_paise=payload.balance_paise, on=payload.on or t,
                                 as_of=t, note=payload.note)
        log(request, s, "CASH_BALANCE_SET", "cash_account", account_id, amount_paise=b.balance_paise,
            on=b.as_of, via="api")
        return {"id": b.id}

    @app.post("/api/cash/planned", status_code=201)
    def api_planned(request: Request, payload: PlannedIn, _: str = Depends(user), s: Session = Depends(db)):
        it = svc.add_planned_item(s, as_of=today(), **payload.model_dump())
        log(request, s, "PLAN_ADDED", "planned_cash_item", it.id, note=it.description,
            amount_paise=it.amount_paise, on=it.item_date, type=it.direction, via="api")
        return {"id": it.id}

    @app.post("/api/cash/planned/{item_id}/cancel")
    def api_planned_cancel(request: Request, item_id: int, _: str = Depends(user), s: Session = Depends(db)):
        it = svc.cancel_planned_item(s, item_id)
        log(request, s, "PLAN_CANCELLED", "planned_cash_item", it.id, note=it.description, via="api")
        return {"id": it.id, "cancelled": True}

    def projection_json(p) -> dict:
        return {"interest_paise": p.interest_paise, "payments_paise": p.payments_paise,
                "topups_paise": p.topups_paise, "principal_left_paise": p.principal_left_paise,
                "interest_left_paise": p.interest_left_paise, "payoff_paise": p.payoff_paise,
                "effective_rate": str(p.effective_rate), "closed_on": str(p.closed_on) if p.closed_on else None,
                "dues": [{"due_date": str(d.due_date), "interest_paise": d.interest_paise, "paid": d.paid}
                         for d in p.dues]}

    @app.post("/api/simulate/loan")
    def api_simulate_loan(payload: SimulateLoanIn, _: str = Depends(user), s: Session = Depends(db)):
        from loan_tracker.simulator import Scenario
        t = today()
        sim = reports.simulate_loan(s, payload.loan_id, t, add_months(t, payload.horizon_months),
                                    Scenario(payload.kind, payload.on or t, amount_paise=payload.amount_paise or 0,
                                             new_rate=payload.new_rate, months=payload.months or 0))
        return {"loan_id": payload.loan_id, "today": str(t), "horizon": str(sim.horizon),
                "actual": projection_json(sim.actual), "simulated": projection_json(sim.simulated),
                "links": [{"link_id": e.link.id, "other_loan_id": e.other.id,
                           "exposure_actual_paise": e.exposure_actual, "exposure_simulated_paise": e.exposure_sim,
                           "spread_actual_paise": e.spread_actual, "spread_simulated_paise": e.spread_sim}
                          for e in sim.links], "saved": False}

    @app.post("/api/simulate/new-loan")
    def api_simulate_new(payload: SimulateNewIn, _: str = Depends(user), s: Session = Depends(db)):
        t = today()
        sim = reports.simulate_new_loan(s, t, payload.days, direction=payload.direction, book=payload.book,
                                        principal_paise=payload.principal_paise, rate=payload.rate,
                                        start_date=payload.start_date or t,
                                        due_day=payload.due_day or (payload.start_date or t).day,
                                        maturity_date=payload.maturity_date, buffer_paise=settings.cash_buffer_paise)
        return {"monthly_interest_paise": sim.monthly_interest_paise,
                "interest_in_window_paise": sim.interest_in_window_paise,
                "flows": [{"date": str(d), "amount_paise": a} for d, a in sim.flows],
                "before": forecast_json(sim.before), "after": forecast_json(sim.after), "saved": False}

    def txn_json(t, suggestions=None) -> dict:
        out = {"id": t.id, "import_id": t.import_id, "account_id": t.account_id, "txn_date": str(t.txn_date),
               "description": t.description, "reference": t.reference, "amount_paise": t.amount_paise,
               "balance_paise": t.balance_paise, "status": t.status, "ledger_entry_id": t.ledger_entry_id,
               "note": t.note}
        if suggestions is not None:
            out["suggestions"] = [{"kind": x.kind, "loan_id": x.loan_id, "entry_id": x.entry_id, "score": x.score,
                                   "strong": x.strong, "reason": x.reason} for x in suggestions]
        return out

    @app.get("/api/bank/imports")
    def api_bank_imports(_: str = Depends(user), s: Session = Depends(db)):
        return [{"id": i.id, "account_id": i.account_id, "account": i.account.name, "file_name": i.file_name,
                 "row_count": i.row_count, "new_count": i.new_count, "period_start": str(i.period_start),
                 "period_end": str(i.period_end)} for i in svc.bank_imports(s)]

    @app.get("/api/bank/imports/{import_id}")
    def api_bank_import(import_id: int, _: str = Depends(user), s: Session = Depends(db)):
        txns = svc.bank_txns(s, import_id)
        sugg = svc.txn_suggestions(s, today(), txns)
        return [txn_json(t, sugg.get(t.id)) for t in txns]

    @app.post("/api/bank/txns/{txn_id}/link")
    def api_bank_link(request: Request, txn_id: int, payload: BankLinkIn, _: str = Depends(user),
                      s: Session = Depends(db)):
        t = svc.link_bank_txn(s, txn_id, payload.entry_id)
        log(request, s, "BANK_LINKED", "bank_txn", t.id, entry=payload.entry_id, via="api")
        return txn_json(t)

    @app.post("/api/bank/txns/{txn_id}/record", status_code=201)
    def api_bank_record(request: Request, txn_id: int, payload: BankRecordIn, _: str = Depends(user),
                        s: Session = Depends(db)):
        t, e = svc.record_bank_txn(s, txn_id, payload.loan_id, today())
        log(request, s, "ENTRY_POSTED", "ledger_entry", e.id, loan_id=payload.loan_id, type=e.type,
            amount_paise=e.amount_paise, entry_date=e.entry_date, via="api bank import")
        return txn_json(t) | {"entry_id": e.id}

    @app.post("/api/bank/txns/{txn_id}/ignore")
    def api_bank_ignore(request: Request, txn_id: int, payload: ReverseIn, _: str = Depends(user),
                        s: Session = Depends(db)):
        t = svc.ignore_bank_txn(s, txn_id, payload.note)
        log(request, s, "BANK_IGNORED", "bank_txn", t.id, note=t.note, via="api")
        return txn_json(t)

    @app.post("/api/bank/txns/{txn_id}/undo")
    def api_bank_undo(request: Request, txn_id: int, _: str = Depends(user), s: Session = Depends(db)):
        t = svc.undo_bank_txn(s, txn_id)
        log(request, s, "BANK_UNDONE", "bank_txn", t.id, via="api")
        return txn_json(t)

    def doc_json(d) -> dict:
        return {"id": d.id, "party_id": d.party_id, "loan_id": d.loan_id, "kind": d.kind, "title": d.title,
                "file_name": d.file_name, "content_type": d.content_type, "size_bytes": d.size_bytes,
                "sha256": d.sha256, "uploaded_by": d.uploaded_by, "created_at": d.created_at.isoformat(),
                "url": f"/api/documents/{d.id}/file"}

    @app.get("/api/documents")
    def api_documents(party_id: int | None = None, loan_id: int | None = None, _: str = Depends(owner_only),
                      s: Session = Depends(db)):
        return [doc_json(d) for d in svc.documents(s, party_id=party_id, loan_id=loan_id)]

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

    from loan_tracker.web import views
    views.register(app, SimpleNamespace(
        db=db, user=user, owner_only=owner_only, log=log, today=today, book_of=book_of, parse_date=parse_date,
        csrf_token=csrf_token, settings=settings, vault=vault, receipt_of=receipt_of, issuer_for=issuer_for,
        recent_failures=recent_failures, who=who, receipt_json=receipt_json, PUBLIC_HEADERS=PUBLIC_HEADERS,
        insecure_secret=insecure_secret, BOOKS=BOOKS, reports_parties=reports_parties))
    serve_spa(app)
    return app


SPA_DIR = HERE / "spa"
SPA_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; "
                               "frame-src 'self' blob:; object-src 'none'; base-uri 'none'; form-action 'self'; "
                               "frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
}


def serve_spa(app: FastAPI) -> None:
    """The built React app (frontend/ -> loan_tracker/web/spa). Any path that is not an API
    or file route gets index.html, so the browser's router can show the screen. Borrower
    links (/s/...) also get no-store and noindex."""
    index = SPA_DIR / "index.html"
    if (SPA_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=SPA_DIR / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        if path.startswith("api/") or not index.exists():
            return JSONResponse({"detail": "not found"}, status_code=404)
        headers = dict(SPA_HEADERS)
        headers["Cache-Control"] = "no-cache"
        if path.startswith("s/"):
            headers.update({"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex, nofollow"})
        return Response(index.read_bytes(), media_type="text/html", headers=headers)


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
