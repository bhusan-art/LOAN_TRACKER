"""Web app end to end through the JSON API the React app uses: login with 2FA, CSRF header,
create a loan, pay, reverse, every screen's payload, viewers, documents, borrower links.
(Ported from the server-rendered page tests in Phase 17; expected numbers unchanged.)"""
import re
from datetime import date as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from loan_tracker import auth
from loan_tracker import services as svc
from loan_tracker.config import Settings
from loan_tracker.db import models as m
from loan_tracker.web.app import READ_ONLY_POSTS, create_app

SECRET = auth.new_totp_secret()


def make_client(s, **settings) -> TestClient:
    maker = sessionmaker(bind=s.connection(), join_transaction_mode="create_savepoint", expire_on_commit=False)
    return TestClient(create_app(Settings(**({"secret_key": "test"} | settings)), maker=maker,
                                 today_fn=lambda: D(2026, 4, 1)))


@pytest.fixture()
def client(s):
    s.add(m.AppUser(username="owner", password_hash=auth.hash_password("a long password"), totp_secret=SECRET))
    s.flush()
    return make_client(s)


def arm(c) -> TestClient:
    """Fetch the session's CSRF token and send it on every later request, as the React app does."""
    c.headers["X-CSRF-Token"] = c.get("/api/session").json()["csrf"]
    return c


def login(c, code=None, username="owner", password="a long password"):
    arm(c)
    r = c.post("/api/login", json={"username": username, "password": password, "code": code or auth.totp_code(SECRET)})
    if r.status_code == 200:
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def new_loan(c, name="Ravi", **kw) -> tuple[int, int]:
    pid = c.post("/api/parties", json={"name": name} | kw.pop("party", {})).json()["id"]
    body = {"party_id": pid, "direction": "GIVEN", "book": "BUSINESS", "principal_paise": 100_000,
            "start_date": "2026-01-01", "rate": "2"} | kw
    return pid, c.post("/api/loans", json=body).json()["id"]


def labels(c, loan_id=None) -> list[str]:
    return [r["label"] for r in c.get("/api/ui/activity" + (f"?loan_id={loan_id}" if loan_id else "")).json()]


def test_pages_need_login(client):
    assert client.get("/api/ui/home").status_code == 401
    assert client.get("/api/loans").status_code == 401
    assert client.get("/api/session").json()["user"] is None


def test_wrong_code_rejected(client):
    r = login(client, code="000000")
    assert r.status_code == 401 and "Wrong username, password or code." in r.json()["detail"]
    assert client.get("/api/session").json()["user"] is None


def test_full_flow(client):
    assert login(client).json()["user"] == "owner"
    party_id = client.post("/api/parties", json={"name": "Ravi"}).json()["id"]
    loan_id = client.post("/api/ui/loans", json={"party_id": party_id, "direction": "GIVEN", "book": "PERSONAL",
                                                 "principal": "1,00,000", "start_date": "2026-01-01", "rate": "2",
                                                 "due_day": "1"}).json()["id"]
    client.post(f"/api/ui/loans/{loan_id}/entries", json={"type": "PAYMENT", "amount": "1500", "entry_date": "2026-02-05"})
    msg = client.post("/api/ui/quick", json={"loan_id": loan_id, "amount": "32,500", "entry_date": "2026-03-15"}).json()["message"]
    assert "principal ₹30,000.00" in msg
    data = client.get(f"/api/loans/{loan_id}").json()
    assert data["balances"]["payoff_today_paise"] == 7_167_097
    assert data["principal_outstanding_paise"] == 7_000_000
    page = client.get(f"/api/ui/loans/{loan_id}").json()
    assert page["balances"]["payoff_today_paise"] == 7_167_097 and page["brief"]["principal_outstanding_paise"] == 7_000_000

    first_payment = client.get(f"/api/loans/{loan_id}/entries").json()[-2]
    assert first_payment["amount_paise"] == 150_000
    assert client.post(f"/api/ui/entries/{first_payment['id']}/reverse", json={}).status_code == 200
    assert client.get(f"/api/loans/{loan_id}").json()["principal_outstanding_paise"] == 7_150_000

    for url in ["/api/ui/home", "/api/ui/loans", "/api/ui/parties", f"/api/ui/parties/{party_id}",
                f"/api/ui/loans/{loan_id}", "/api/ui/calendar?days=60", "/api/ui/calendar?days=90", "/api/ui/reports",
                f"/api/ui/statement/{loan_id}", "/api/ui/funding", "/api/ui/parties-min", "/api/ui/payments",
                "/api/search?q=ra", "/api/ui/collections", "/api/ui/cash", "/api/ui/simulate", "/api/ui/bank",
                "/api/ui/receipts", "/api/ui/activity", "/api/dashboard", "/api/calendar", "/api/reports/fy-interest",
                "/api/reports/overdue", "/api/reminders", "/api/parties", "/healthz"]:
        assert client.get(url).status_code == 200, url
    csv = client.get("/export.csv")
    assert csv.headers["content-type"].startswith("text/csv")


def test_payments_and_search(client):
    login(client)
    pid, lid = new_loan(client, "Ravi Kumar", party={"phone": "98480 12345"}, principal_paise=10_000_000, due_day=1)
    client.post(f"/api/ui/loans/{lid}/entries", json={"type": "PAYMENT", "amount": "2,000", "entry_date": "2026-02-01"})
    [p] = client.get("/api/ui/payments").json()
    assert (p["direction"], p["amount_paise"], p["split"]["INTEREST"], p["receipt"]["number"]) == ("IN", 200_000, 200_000, "RCPT-2026-0001")
    found = client.get("/api/search?q=ravi").json()
    assert found["people"][0]["id"] == pid and found["loans"][0]["id"] == lid
    assert client.get(f"/api/search?q=LN-{lid}").json()["loans"][0]["id"] == lid
    assert client.get("/api/search?q=RCPT-2026").json()["receipts"][0]["number"] == "RCPT-2026-0001"
    assert client.get("/api/search?q=r").json() == {"people": [], "loans": [], "receipts": []}


def test_book_toggle_filters(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Suresh"}).json()["id"]
    client.post("/api/loans", json={"party_id": pid, "direction": "TAKEN", "book": "BUSINESS",
                                    "principal_paise": 10_000_000, "start_date": "2026-01-01", "rate": "1.5"})
    assert len(client.get("/api/loans?book=business").json()) == 1
    assert client.get("/api/loans?book=personal").json() == []
    client.cookies.set("book", "personal")
    assert client.get("/api/session").json()["book"] == "personal"
    assert client.get("/api/ui/loans").json() == []
    client.cookies.set("book", "all")
    assert client.get("/api/ui/loans").json()[0]["rate"] == "1.5"          # rates are trimmed: not "1.5000"


def test_validation_errors_are_messages(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "X"}).json()["id"]
    r = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "",
                                        "principal_paise": 1, "start_date": "2026-01-01", "rate": "2"})
    assert r.status_code == 400 and "book" in r.json()["detail"]
    r = client.post("/api/ui/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                           "principal": "1.005", "rate": "2"})
    assert r.status_code == 400 and "2 decimal places" in r.json()["detail"]


def test_login_rate_limited(client):
    for _ in range(5):
        login(client, code="000000")
    r = client.post("/api/login", json={"username": "owner", "password": "x"})
    assert r.status_code == 429 and "Too many attempts" in r.json()["detail"]


def test_request_without_csrf_token_is_rejected(client):
    login(client)
    token = client.headers.pop("X-CSRF-Token")
    r = client.post("/api/parties", json={"name": "Sneaky"})
    assert r.status_code == 403 and "expired" in r.json()["detail"]
    assert client.post("/api/parties", json={"name": "Sneaky"}, headers={"X-CSRF-Token": "guess"}).status_code == 403
    # file uploads and form bodies too: the header is the guard, whatever the body
    assert client.post("/api/documents", files={"file": ("x.pdf", b"%PDF-", "application/pdf")}).status_code == 403
    assert client.post("/api/parties", data={"name": "Sneaky"}).status_code == 403
    client.headers["X-CSRF-Token"] = token
    assert client.get("/api/parties").json() == []
    assert client.post("/api/parties", json={"name": "Real"}).status_code == 201


def test_unknown_api_path_is_json_404(client):
    login(client)
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["detail"] == "not found"


def test_future_start_date_rejected(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Later"}).json()["id"]
    r = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                        "principal_paise": 100, "start_date": "2026-05-01", "rate": "2"})
    assert r.status_code == 400 and "future" in r.json()["detail"]


def test_api_rejects_bad_shapes_with_readable_400(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Shape"}).json()["id"]
    base = {"party_id": pid, "direction": "GIVEN", "book": "BUSINESS", "principal_paise": 100_000,
            "start_date": "2026-01-01", "rate": "2"}
    for bad, field in [({"principal_paise": 1000.5}, "principal_paise"),      # money is integer paise
                       ({"principal_paise": "1000"}, "principal_paise"),      # no silent string -> int
                       ({"principal_paise": 0}, "principal_paise"),
                       ({"direction": "LENT"}, "direction"),
                       ({"rate": "-1"}, "rate"),
                       ({"due_day": 32}, "due_day"),
                       ({"start_date": "01/01/2026"}, "start_date"),
                       ({"princpal": 5}, "princpal")]:                         # typos are not ignored
        r = client.post("/api/loans", json=base | bad)
        assert r.status_code == 400, bad
        assert field in r.json()["detail"] and r.json()["errors"][0]["field"] == field
    assert client.get("/api/loans").json() == []
    assert client.post("/api/loans", json=base).status_code == 201


def test_api_entry_and_funding_shapes(client):
    login(client)
    _, lid = new_loan(client, "E")
    assert client.post(f"/api/loans/{lid}/entries", json={"type": "DISBURSE", "amount_paise": 5}).status_code == 400
    assert client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 5.0}).status_code == 400
    ok = client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 500,
                                                        "entry_date": "2026-01-20", "payment_mode": "UPI"})
    assert ok.status_code == 201
    assert client.post("/api/funding", json={"taken_loan_id": lid, "given_loan_id": lid}).status_code == 400
    assert client.post(f"/api/entries/{ok.json()['id']}/reverse", json={"note": "typo"}).status_code == 201


def test_form_missing_field_shows_message(client):
    login(client)
    r = client.post("/api/ui/funding", json={"amount": "5"})
    assert r.status_code == 400 and "taken_loan_id" in r.json()["detail"]


def test_audit_log_records_changes_and_logins(client):
    login(client, code="000000")                       # one failed attempt first
    login(client)
    _, lid = new_loan(client, "Audit")
    client.post(f"/api/ui/loans/{lid}/entries", json={"type": "PAYMENT", "amount": "100", "entry_date": "2026-01-20"})
    eid = client.get(f"/api/loans/{lid}/entries").json()[0]["id"]
    client.post(f"/api/ui/entries/{eid}/reverse", json={"note": "wrong loan"})
    client.post(f"/api/loans/{lid}/status", json={"status": "SETTLED"})
    rows = client.get("/api/ui/activity").json()
    got = [r["label"] for r in rows]
    for label in ["Failed login", "Logged in", "Person added", "Loan created", "Entry recorded",
                  "Entry reversed", "Status changed"]:
        assert label in got, label
    text = str(rows)
    assert "wrong loan" in text and "SETTLED" in text and "a long password" not in text
    history = labels(client, lid)
    assert "Entry reversed" in history and "Person added" not in history


def test_failed_change_leaves_no_audit_row(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Nope"}).json()["id"]
    client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                    "principal_paise": 100, "start_date": "2026-05-01", "rate": "2"})
    assert "Loan created" not in labels(client)


def test_collections_pages_and_api(client):
    login(client)
    _, lid = new_loan(client, "Ravi Kumar", party={"phone": "98480 12345"}, principal_paise=10_000_000, due_day=1)
    page = client.get(f"/api/ui/loans/{lid}").json()
    assert page["chasing"] and page["whatsapp"].startswith("https://wa.me/919848012345?text=Hello%20Ravi")
    r = client.post(f"/api/ui/loans/{lid}/contacts", json={"kind": "CALL", "outcome": "PROMISED", "note": "will pay",
                                                          "promise_amount": "2,000", "promise_date": "2026-04-05",
                                                          "contact_date": "2026-03-30"})
    assert "is tracked against real payments" in r.json()["message"]
    assert client.get(f"/api/ui/loans/{lid}").json()["promises"][0]["state"]["status"] == "PENDING"
    assert len(client.get("/api/ui/collections").json()["promises_due"]) == 1
    data = client.get(f"/api/loans/{lid}/contacts").json()
    assert data["promises"][0]["status"] == "PENDING" and data["contacts"][0]["outcome"] == "PROMISED"
    pr = data["promises"][0]["id"]
    assert client.post(f"/api/promises/{pr}/cancel", json={"note": "changed plan"}).json()["cancelled"]
    assert client.post(f"/api/promises/{pr}/cancel", json={}).status_code == 400
    bad = client.post(f"/api/loans/{lid}/contacts", json={"outcome": "LATER"})
    assert bad.status_code == 400 and "outcome" in bad.json()["detail"]
    assert client.post(f"/api/loans/{lid}/contacts", json={"kind": "VISIT", "outcome": "NO_ANSWER",
                                                          "next_follow_up": "2026-04-02"}).status_code == 201
    assert len(client.get("/api/collections").json()["follow_ups"]) == 0   # follow-up date not reached
    got = labels(client)
    assert "Contact logged" in got and "Promise made" in got and "Promise cancelled" in got


def test_settled_loan_is_not_chased(client):
    """UI review: settled/closed loans offered a WhatsApp reminder and 'Log a contact'."""
    login(client)
    _, lid = new_loan(client, "Ravi", party={"phone": "98480 12345"})
    client.post(f"/api/loans/{lid}/status", json={"status": "SETTLED"})
    page = client.get(f"/api/ui/loans/{lid}").json()
    assert page["status"] == "SETTLED" and not page["chasing"] and page["can_close"]


PUBLIC = {"/healthz", "/manifest.webmanifest", "/sw.js", "/api/session", "/api/login", "/api/logout",
          # the React app's own page (index.html) for every non-API path: it has no data in it
          "/{path:path}",
          # borrower statement links: no login, the unguessable token is the key (tested separately)
          "/api/public/s/{token}", "/api/public/s/{token}/receipts/{receipt_id}"}


def test_every_route_needs_login(client):
    """Regression guard: a new API route that forgets Depends(user) fails here."""
    arm(client)
    checked = 0
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if path in PUBLIC or path.startswith(("/static", "/assets")) or not getattr(route, "methods", None):
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = client.request(method, url, follow_redirects=False, json={})
            assert r.status_code in (401, 403), f"{method} {path} -> {r.status_code}"
            checked += 1
    assert checked > 70


def test_api_docs_after_login(client):
    login(client)
    assert "/api/loans" in client.get("/api/openapi.json").json()["paths"]
    assert client.get("/api/docs").status_code == 200


def test_receipts_and_certificate_pages(client):
    login(client)
    _, lid = new_loan(client, "Ravi Kumar", party={"phone": "98480 12345"}, principal_paise=10_000_000, due_day=1,
                      collateral="gold chain")
    r = client.post(f"/api/ui/loans/{lid}/entries", json={"type": "PAYMENT", "amount": "2,000", "entry_date": "2026-02-01",
                                                         "payment_mode": "UPI", "reference": "UPI123"}).json()
    assert r["message"].endswith("Receipt RCPT-2026-0001 issued.")
    rid = client.get("/api/receipts").json()[0]["id"]
    assert r["receipt_id"] == rid
    page = client.get(f"/api/ui/receipts/{rid}").json()
    assert (page["party"], page["amount_in_words"], page["reference"]) == ("Ravi Kumar", "Rupees Two Thousand Only", "UPI123")
    assert page["whatsapp"].startswith("https://wa.me/919848012345?text=Receipt%20RCPT-2026-0001")
    assert page["issuer"]["name"] == ""                              # the screen shows the RECEIPT_NAME hint
    assert client.get(f"/api/receipts/{rid}").json()["amount_in_words"] == "Rupees Two Thousand Only"
    assert client.get("/api/ui/receipts?q=ravi").json()["receipts"][0]["number"] == "RCPT-2026-0001"
    assert client.get("/api/ui/receipts?q=nobody").json()["receipts"] == []

    # not closed yet -> no certificate
    r = client.get(f"/api/ui/closure/{lid}")
    assert r.status_code == 400 and "not closed yet" in r.json()["detail"]
    payoff = client.get(f"/api/loans/{lid}").json()["balances"]["payoff_today_paise"]
    client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": payoff, "entry_date": "2026-04-01"})
    cert = client.get(f"/api/ui/closure/{lid}").json()
    assert cert["status"] == "CLOSED" and cert["receipts"] == 2 and cert["loan"]["collateral"] == "gold chain"
    assert client.get(f"/api/ui/loans/{lid}").json()["can_close"]

    # reversal keeps the receipt, marked cancelled, and the certificate goes away
    eid = client.get("/api/receipts").json()[0]["ledger_entry_id"]
    client.post(f"/api/entries/{eid}/reverse", json={"note": "bounced"})
    latest = client.get("/api/receipts").json()[0]
    assert latest["cancelled"] and "bounced" in latest["cancel_reason"]
    assert client.get(f"/api/ui/receipts/{latest['id']}").json()["cancelled"]
    assert client.get(f"/api/ui/closure/{lid}").status_code == 400


def test_receipt_issuer_name_from_settings(s):
    s.add(m.AppUser(username="owner", password_hash=auth.hash_password("a long password"), totp_secret=SECRET))
    s.flush()
    c = make_client(s, receipt_name="A. Owner", receipt_business_name="Example Finance",
                    receipt_details="Main Road · 98480 00000")
    login(c)
    pid = c.post("/api/parties", json={"name": "P"}).json()["id"]
    for book in ("BUSINESS", "PERSONAL"):
        lid = c.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": book,
                                         "principal_paise": 100_000, "start_date": "2026-01-01",
                                         "rate": "2"}).json()["id"]
        c.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 500})
    biz, per = sorted(c.get("/api/receipts").json(), key=lambda r: r["number"])
    assert c.get(f"/api/ui/receipts/{biz['id']}").json()["issuer"]["name"] == "Example Finance"
    assert c.get(f"/api/ui/receipts/{per['id']}").json()["issuer"] == {"name": "A. Owner", "details": "Main Road · 98480 00000"}


def test_cash_pages_and_api(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Suresh"}).json()["id"]
    client.post("/api/loans", json={"party_id": pid, "direction": "TAKEN", "book": "BUSINESS",
                                    "principal_paise": 10_000_000, "start_date": "2026-03-05", "rate": "1.5",
                                    "due_day": 5})
    assert client.get("/api/ui/cash").json()["forecast"]["accounts"] == []
    r = client.post("/api/ui/cash/accounts", json={"name": "SBI", "kind": "BANK", "book": "BUSINESS", "balance": "1,000"})
    assert r.json()["message"] == "Account SBI added."
    data = client.get("/api/cash").json()
    assert data["opening_paise"] == 100_000 and data["accounts"][0]["name"] == "SBI"
    # Suresh's Rs 1,500 on 5 Apr is more than the Rs 1,000 in the bank
    assert (data["expected"]["short_date"], data["expected"]["short_paise"]) == ("2026-04-05", 50_000)
    assert "Cash: you may be ₹500.00 short on 05 Apr 2026" in " ".join(client.get("/api/ui/cash").json()["warnings"])
    assert "Cash: you may be ₹500.00 short" in " ".join(client.get("/api/ui/home").json()["warning_text"])
    acc = data["accounts"][0]["id"]
    client.post(f"/api/ui/cash/accounts/{acc}/balance", json={"balance": "-2,000", "on": "2026-04-01"})
    assert client.get("/api/cash").json()["opening_paise"] == -200_000
    client.post("/api/ui/cash/planned", json={"description": "FD maturing", "amount": "5,000", "item_date": "2026-04-03",
                                              "direction": "IN", "book": "BUSINESS"})
    assert [p["description"] for p in client.get("/api/ui/cash").json()["planned"]] == ["FD maturing"]
    assert client.get("/api/cash").json()["expected"]["short_date"] == "2026-04-01"   # overdrawn today
    cal = client.get("/api/ui/calendar").json()
    planned = [i for d in cal["calendar"] for i in d["money_in"] if i["party"] == "FD maturing"]
    assert planned and planned[0]["loan_id"] == 0 and cal["opening_paise"] == -200_000
    # API with typed bodies
    bad = client.post("/api/cash/planned", json={"book": "BUSINESS", "item_date": "2026-04-03",
                                                 "direction": "SIDEWAYS", "amount_paise": 1, "description": "x"})
    assert bad.status_code == 400 and "direction" in bad.json()["detail"]
    pl = client.post("/api/cash/planned", json={"book": "BUSINESS", "item_date": "2026-04-03", "direction": "OUT",
                                                "amount_paise": 100, "description": "tea"}).json()["id"]
    assert client.post(f"/api/cash/planned/{pl}/cancel", json={}).json()["cancelled"]
    assert client.post(f"/api/cash/accounts/{acc}/balance", json={"balance_paise": 5}).status_code == 201
    assert "closed." in client.post(f"/api/cash/accounts/{acc}/active", json={"active": False}).json()["message"]
    assert client.get("/api/ui/cash").json()["closed_accounts"][0]["name"] == "SBI"
    got = labels(client)
    assert all(x in got for x in ("Account added", "Balance updated", "Plan added", "Plan cancelled", "Account closed"))


def test_simulator_page_and_api(client):
    login(client)
    _, lid = new_loan(client, "Ravi", principal_paise=10_000_000, start_date="2026-04-01", due_day=1)
    assert [x["id"] for x in client.get("/api/ui/simulate").json()["loans"]] == [lid]
    r = client.post("/api/ui/simulate", json={"mode": "loan", "loan_id": lid, "kind": "EXTRA_PAYMENT", "on": "2026-05-01",
                                              "amount": "50,000", "horizon": 6}).json()["result"]
    # Jun-Oct dues on half the principal: Rs 1,000 less each
    assert r["actual"]["interest_paise"] - r["simulated"]["interest_paise"] == 500_000
    entries_before = len(client.get(f"/api/loans/{lid}/entries").json())
    r = client.post("/api/simulate/loan", json={"loan_id": lid, "kind": "EXTRA_PAYMENT", "on": "2026-05-01",
                                                "amount_paise": 5_000_000, "horizon_months": 6}).json()
    assert r["saved"] is False and r["actual"]["interest_paise"] - r["simulated"]["interest_paise"] == 500_000
    assert len(client.get(f"/api/loans/{lid}/entries").json()) == entries_before
    bad = client.post("/api/ui/simulate", json={"mode": "loan", "loan_id": lid, "kind": "TOPUP", "on": "2026-05-01"})
    assert bad.status_code == 400 and "the amount must be more than zero" in bad.json()["detail"]
    assert client.post("/api/simulate/loan", json={"loan_id": lid, "kind": "LATER"}).status_code == 400
    new = client.post("/api/ui/simulate", json={"mode": "new", "direction": "GIVEN", "amount": "1,00,000", "rate": "2",
                                                "start": "2026-04-15", "due_day": "1", "loan_book": "PERSONAL",
                                                "days": 60}).json()["result"]
    assert new["monthly_interest_paise"] == 200_000 and new["before"]["has_accounts"] is False
    assert client.get("/api/session").json()["book"] == "all"              # the book switch is untouched
    api = client.post("/api/simulate/new-loan", json={"direction": "GIVEN", "book": "BUSINESS",
                                                      "principal_paise": 10_000_000, "rate": "2",
                                                      "start_date": "2026-04-15", "due_day": 1}).json()
    assert api["flows"][0] == {"date": "2026-04-15", "amount_paise": -10_000_000}
    assert api["after"]["expected"]["short_date"] == "2026-04-15"


def test_simulator_explains_arrears_first(client):
    login(client)
    _, lid = new_loan(client, "Late", principal_paise=10_000_000, due_day=1)
    r = client.post("/api/ui/simulate", json={"mode": "loan", "loan_id": lid, "kind": "EXTRA_PAYMENT",
                                              "on": "2026-05-01", "amount": "50000"}).json()["result"]
    assert r["arrears_today_paise"] == 600_000            # ₹6,000.00 of interest is unpaid today


STATEMENT = b"""Date,Narration,Chq./Ref.No.,Withdrawal Amt.,Deposit Amt.,Closing Balance
05/02/26,UPI-RAVI KUMAR-603612345,603612345,,2000.00,26500.00
06/02/26,UPI-RAVI KUMAR-603612999,603612999,,1000.00,27500.00
07/02/26,SWIGGY ORDER,,350.00,,27150.00
"""


def test_bank_import_flow(client):
    login(client)
    _, lid = new_loan(client, "Ravi Kumar", book="PERSONAL", principal_paise=10_000_000, due_day=1)
    first = client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 200_000,
                                                          "entry_date": "2026-02-04"}).json()["id"]
    assert client.get("/api/ui/bank").json()["accounts"] == []
    client.post("/api/ui/cash/accounts", json={"name": "HDFC", "kind": "BANK", "book": "PERSONAL", "balance": "0"})
    acc = client.get("/api/cash").json()["accounts"][0]["id"]
    r = client.post("/api/bank/upload", data={"account_id": str(acc), "update_balance": "1"},
                    files={"file": ("hdfc-feb.csv", STATEMENT, "text/csv")}).json()
    # the account was opened today (1 Apr) with a balance, so a February statement does not overwrite it
    assert r["message"] == "3 new transaction(s) imported. Balance not changed: you have a newer one (01 Apr 2026)."
    imp = r["id"]
    page = client.get(f"/api/ui/bank/imports/{imp}").json()
    assert page["exact"] == 1 and page["counts"]["NEW"] == 3
    reasons = [g["reason"] for t in page["txns"] for g in t["suggestions"]]
    assert any(x.startswith("already recorded") for x in reasons) and any("name 'ravi' in the narration" in x for x in reasons)
    # no token, no upload
    bad = client.post("/api/bank/upload", data={"account_id": str(acc)}, files={"file": ("x.csv", STATEMENT, "text/csv")},
                      headers={"X-CSRF-Token": ""})
    assert bad.status_code == 403
    # link the already-recorded Rs 2,000
    assert "1 bank line(s) linked" in client.post(f"/api/bank/imports/{imp}/link-exact", json={}).json()["message"]
    lines = {t["amount_paise"]: t for t in client.get(f"/api/bank/imports/{imp}").json()}
    assert lines[200_000]["status"] == "LINKED" and lines[200_000]["ledger_entry_id"] == first
    # record the Rs 1,000 as a payment: receipt issued
    t2 = lines[100_000]
    assert t2["suggestions"][0]["loan_id"] == lid
    rec = client.post(f"/api/bank/txns/{t2['id']}/record", json={"loan_id": lid}).json()
    assert rec["status"] == "RECORDED"
    assert [x["amount_paise"] for x in client.get("/api/receipts").json() if x["ledger_entry_id"] == rec["entry_id"]] == [100_000]
    # ignore the Swiggy order, then undo it
    t3 = lines[-35_000]
    client.post(f"/api/bank/txns/{t3['id']}/ignore", json={"note": "food"})
    assert client.post(f"/api/bank/txns/{t3['id']}/undo", json={}).json()["status"] == "NEW"
    assert client.post(f"/api/bank/txns/{t3['id']}/record", json={"loan_id": lid}).status_code == 400  # money out
    assert client.post(f"/api/bank/txns/{t3['id']}/ignore", json={"note": "food"}).json()["status"] == "IGNORED"
    assert client.get(f"/api/ui/bank/imports/{imp}").json()["txns"] == []          # nothing left to review
    every = client.get(f"/api/ui/bank/imports/{imp}?show=all").json()
    assert {t["status"] for t in every["txns"]} == {"LINKED", "RECORDED", "IGNORED"}
    assert client.get("/api/ui/bank").json()["imports"][0]["open"] == 0
    # re-upload: nothing new
    r = client.post("/api/bank/upload", data={"account_id": str(acc)}, files={"file": ("hdfc-feb.csv", STATEMENT, "text/csv")})
    assert r.json()["message"] == "0 new transaction(s) imported; 3 already imported earlier were skipped."
    got = labels(client)
    assert "Statement imported" in got and "Bank line linked" in got and "Bank line ignored" in got


def test_bank_upload_bad_file_shows_message(client):
    login(client)
    client.post("/api/ui/cash/accounts", json={"name": "HDFC", "kind": "BANK", "book": "PERSONAL", "balance": "0"})
    acc = client.get("/api/cash").json()["accounts"][0]["id"]
    r = client.post("/api/bank/upload", data={"account_id": str(acc)},
                    files={"file": ("photo.csv", b"\x89PNG\r\n\x1a\n...", "text/csv")})
    assert r.status_code == 400 and "could not read the statement" in r.json()["detail"]


JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 64


@pytest.fixture()
def doc_client(s, tmp_path):
    from loan_tracker.docvault import new_key
    s.add(m.AppUser(username="owner", password_hash=auth.hash_password("a long password"), totp_secret=SECRET))
    s.flush()
    c = make_client(s, document_key=new_key(), document_dir=str(tmp_path))
    login(c)
    return c


def test_documents_upload_view_delete(doc_client, tmp_path):
    c = doc_client
    pid, lid = new_loan(c, "Ravi")
    assert c.get(f"/api/ui/loans/{lid}").json()["documents"] == []
    r = c.post("/api/documents", data={"kind": "ID_PROOF", "title": "Aadhaar", "loan_id": str(lid)},
               files={"file": ("aadhaar.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 201 and r.json()["message"] == "“Aadhaar” stored, encrypted."
    [doc] = c.get(f"/api/documents?loan_id={lid}").json()
    assert doc["party_id"] == pid and doc["content_type"] == "image/jpeg"
    assert c.get(f"/api/ui/parties/{pid}").json()["documents"][0]["title"] == "Aadhaar"
    assert c.get("/api/ui/documents").json()["documents"][0]["kind_label"] == "ID proof"
    f = c.get(doc["url"])
    assert f.content == JPEG and f.headers["content-type"] == "image/jpeg"
    assert "sandbox" in f.headers["content-security-policy"] and "no-store" in f.headers["cache-control"]
    assert f.headers["x-content-type-options"] == "nosniff"
    assert c.get(doc["url"] + "?download=1").headers["content-disposition"].startswith("attachment")
    # a disguised file is refused
    bad = c.post("/api/documents", data={"kind": "OTHER", "loan_id": str(lid)},
                 files={"file": ("x.pdf", b"<script>alert(1)</script>", "application/pdf")})
    assert bad.status_code == 400 and "only PDF, JPEG, PNG or WebP" in bad.json()["detail"]
    # delete: file gone, record kept, view logged
    assert "the file is erased" in c.post(f"/api/documents/{doc['id']}/delete", json={}).json()["message"]
    assert list(tmp_path.glob("*.ltd")) == [] and c.get(f"/api/documents?loan_id={lid}").json() == []
    gone = c.get(doc["url"])
    assert gone.status_code == 400 and "was deleted" in gone.json()["detail"]
    got = labels(c)
    assert "Document added" in got and "Document opened" in got and "Document deleted" in got


def test_documents_page_has_its_own_upload(doc_client):
    """UI review: the Documents page had no way to add a document."""
    c = doc_client
    assert c.get("/api/ui/documents").json()["people"] == []
    pid = c.post("/api/parties", json={"name": "Ravi"}).json()["id"]
    assert c.get("/api/ui/documents").json()["people"] == [{"id": pid, "name": "Ravi"}]
    r = c.post("/api/documents", data={"party_id": str(pid), "kind": "ID_PROOF", "title": "PAN card"},
               files={"file": ("pan.jpg", JPEG, "image/jpeg")})
    assert r.json()["message"] == "“PAN card” stored, encrypted." and r.json()["party_name"] == "Ravi"


def test_documents_switched_off_without_key(client):
    login(client)
    assert "DOCUMENT_KEY is not set" in client.get("/api/ui/documents").json()["problem"]
    s = client.get("/api/session").json()
    assert s["documents_ok"] is False and "DOCUMENT_KEY is not set" in s["documents_problem"]


def test_borrower_statement_link(client, s):
    login(client)
    pid = client.post("/api/parties", json={"name": "Ravi Kumar", "phone": "98480 12345",
                                            "notes": "PRIVATE-NOTE owes others"}).json()["id"]
    lid = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                          "principal_paise": 10_000_000, "start_date": "2026-01-01", "rate": "2",
                                          "due_day": 1, "collateral": "gold chain"}).json()["id"]
    client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 200_000, "entry_date": "2026-02-01"})
    client.post(f"/api/loans/{lid}/contacts", json={"outcome": "PROMISED", "note": "CALL-NOTE wife unwell",
                                                    "promise_amount_paise": 100, "promise_date": "2026-04-05",
                                                    "contact_date": "2026-03-30"})
    other = client.post("/api/parties", json={"name": "Zeta Other"}).json()["id"]
    olid = client.post("/api/loans", json={"party_id": other, "direction": "GIVEN", "book": "PERSONAL",
                                           "principal_paise": 500_000, "start_date": "2026-01-01", "rate": "3"}).json()["id"]
    client.post(f"/api/loans/{olid}/entries", json={"type": "PAYMENT", "amount_paise": 100, "entry_date": "2026-02-01"})
    other_receipt = [r for r in client.get("/api/receipts").json() if r["loan_id"] == olid][0]["id"]

    made = client.post(f"/api/loans/{lid}/share", json={"days": 30}).json()
    assert "shown only once" in made["message"] and made["whatsapp"].startswith("https://wa.me/919848012345")
    assert made["url"].startswith("http://testserver/s/")              # PUBLIC_URL unset: the current address
    token = made["url"].rsplit("/", 1)[1]
    assert token not in client.get(f"/api/ui/loans/{lid}").text        # shown once only

    anon = TestClient(client.app)                                      # the borrower: no login, no cookie
    page = anon.get(f"/api/public/s/{token}")
    assert page.status_code == 200
    st = page.json()["statement"]
    assert st["borrower"] == "Ravi Kumar" and st["loan_id"] == lid
    assert st["payments"][0]["amount_paise"] == 200_000 and st["payments"][0]["receipt_number"].startswith("RCPT-2026-")
    for secret in ("PRIVATE-NOTE", "CALL-NOTE", "Zeta Other", "BUSINESS", "gold chain", "98480", "csrf"):
        assert secret not in page.text, secret
    assert "set-cookie" not in page.headers
    h = page.headers
    assert h["cache-control"].startswith("no-store") and "noindex" in h["x-robots-tag"]
    assert "default-src 'none'" in h["content-security-policy"] and h["referrer-policy"] == "no-referrer"
    rid = [x for x in client.get("/api/receipts").json() if x["loan_id"] == lid][0]["id"]
    assert anon.get(f"/api/public/s/{token}/receipts/{rid}").json()["amount_in_words"] == "Rupees Two Thousand Only"
    assert anon.get(f"/api/public/s/{token}/receipts/{other_receipt}").status_code == 404     # not their receipt
    assert anon.get(f"/api/ui/loans/{lid}").status_code == 401         # the app itself stays closed

    # owner sees the views; switching off works at once
    [share] = client.get(f"/api/ui/loans/{lid}").json()["shares"]
    assert share["view_count"] == 1 and share["live"]
    assert "switched off" in client.post(f"/api/share/{share['id']}/revoke", json={}).json()["message"]
    gone = anon.get(f"/api/public/s/{token}")
    assert gone.status_code == 410 and "switched off" in gone.json()["detail"] and "Ravi" not in gone.text
    assert "Borrower viewed" in labels(client)


def test_spa_page_for_borrower_link_is_private():
    """The React page itself (index.html) for /s/... is never cached or indexed."""
    from loan_tracker.web import app as webapp
    headers = webapp.SPA_HEADERS
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"] and headers["Referrer-Policy"] == "no-referrer"


def test_statement_link_guessing_is_limited(client):
    anon = TestClient(client.app)
    codes = [anon.get(f"/api/public/s/guess{i:04d}").status_code for i in range(32)]
    assert codes[:30] == [404] * 30 and codes[-1] == 429


def test_failure_counters_forget_old_addresses(client):
    import time as _t
    anon = TestClient(client.app)
    anon.get("/api/public/s/nope0001")
    table = client.app.state.failed_shares
    assert len(table) == 1
    for q in table.values():                       # pretend that failure was 16 minutes ago
        q[0] -= 16 * 60
    table["198.51.100.7"].append(_t.time() - 20 * 60)
    anon.get("/api/public/s/nope0002")
    assert len(table) == 1 and len(next(iter(table.values()))) == 1


def test_business_loan_statement_uses_business_name(s):
    from datetime import datetime, timezone
    s.add(m.AppUser(username="owner", password_hash=auth.hash_password("a long password"), totp_secret=SECRET))
    s.flush()
    maker = sessionmaker(bind=s.connection(), join_transaction_mode="create_savepoint", expire_on_commit=False)
    c = TestClient(create_app(Settings(secret_key="t", receipt_name="A. Owner", receipt_business_name="Example Finance"),
                              maker=maker, today_fn=lambda: D(2026, 4, 1)))
    login(c)
    _, lid = new_loan(c, "Ravi")
    with maker() as s2:
        _, token = svc.create_share_link(s2, lid, days=7, actor="o", now=datetime.now(timezone.utc))
        s2.commit()
    page = TestClient(c.app).get(f"/api/public/s/{token}")
    assert page.json()["issuer"]["name"] == "Example Finance" and "A. Owner" not in page.text


def _viewer_client(client, s):
    s.add(m.AppUser(username="acct", password_hash=auth.hash_password("acct pass word 1"), totp_secret=SECRET,
                    role="VIEWER"))
    s.flush()
    v = TestClient(client.app)
    r = login(v, username="acct", password="acct pass word 1")
    assert r.status_code == 200 and r.json()["role"] == "VIEWER"
    assert v.get("/api/session").json()["role"] == "VIEWER"
    return v


def test_viewer_can_read_everything_and_change_nothing(client, s):
    login(client)
    pid, lid = new_loan(client, "Ravi")
    eid = client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 500}).json()["id"]
    v = _viewer_client(client, s)
    for path in ("/api/ui/home", "/api/ui/loans", f"/api/ui/loans/{lid}", "/api/ui/parties", f"/api/ui/parties/{pid}",
                 "/api/ui/collections", "/api/ui/payments", "/api/ui/cash", "/api/ui/calendar", "/api/ui/reports",
                 "/api/ui/receipts", "/api/ui/activity", "/api/ui/simulate", "/api/ui/funding", "/export.csv"):
        assert v.get(path).status_code == 200, path
    assert v.get("/api/loans").json()[0]["id"] == lid
    # every write is refused, whatever route
    before = len(client.get(f"/api/loans/{lid}/entries").json())
    assert v.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 1}).status_code == 403
    assert v.post(f"/api/entries/{eid}/reverse", json={}).status_code == 403
    assert v.post("/api/parties", json={"name": "X"}).status_code == 403
    r = v.post(f"/api/ui/loans/{lid}/entries", json={"type": "PAYMENT", "amount": "1"})
    assert r.status_code == 403 and "Read-only access" in r.json()["detail"]
    assert len(client.get(f"/api/loans/{lid}/entries").json()) == before
    # documents are owner-only, not even listed
    assert v.get("/api/ui/documents").status_code == 403
    assert v.get("/api/documents").status_code == 403
    assert v.get(f"/api/ui/loans/{lid}").json()["documents"] is None
    assert v.get(f"/api/ui/parties/{pid}").json()["documents"] is None
    assert v.get("/api/session").json()["documents_ok"] is False


def test_every_write_route_refuses_viewers(client, s):
    """Regression guard: walk every non-GET route as a viewer; none may get through."""
    v = _viewer_client(client, s)
    checked = 0
    for route in client.app.routes:
        path = getattr(route, "path", "")
        methods = (getattr(route, "methods", None) or set()) - {"GET", "HEAD", "OPTIONS"}
        if not methods or path in ("/api/login", "/api/logout") or path in READ_ONLY_POSTS:
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        for method in methods:
            r = v.request(method, url, follow_redirects=False, json={})
            assert r.status_code == 403, f"{method} {path} -> {r.status_code}"
            checked += 1
    assert checked >= 35          # every write route (HTML form routes are gone since Phase 17)


def test_viewer_can_use_the_what_if_simulator(client, s):
    """Review: the simulator became a POST and the read-only guard refused it to viewers.
    It saves nothing, so viewers may use it (as they could the old GET page)."""
    login(client)
    _, lid = new_loan(client, "Ravi", principal_paise=10_000_000, start_date="2026-04-01", due_day=1)
    v = _viewer_client(client, s)
    before = len(client.get(f"/api/loans/{lid}/entries").json())
    r = v.post("/api/ui/simulate", json={"mode": "loan", "loan_id": lid, "kind": "EXTRA_PAYMENT", "on": "2026-05-01",
                                         "amount": "50,000", "horizon": 6})
    assert r.status_code == 200 and r.json()["result"]["actual"]["interest_paise"] > 0
    assert v.post("/api/simulate/loan", json={"loan_id": lid, "kind": "PAYOFF", "on": "2026-05-01"}).status_code == 200
    assert len(client.get(f"/api/loans/{lid}/entries").json()) == before                # nothing saved
    assert v.post("/api/ui/simulate", json={"mode": "loan"}).status_code == 400          # still validated


def test_disabled_user_and_password_reset_end_sessions(client, s):
    login(client)
    assert client.get("/api/ui/loans").status_code == 200
    owner = s.scalar(__import__("sqlalchemy").select(m.AppUser).where(m.AppUser.username == "owner"))
    owner.session_epoch += 1                     # what a password reset does
    s.flush()
    assert client.get("/api/ui/loans").status_code == 401
    assert client.get("/api/session").json()["user"] is None
    login(client)
    s.add(m.AppUser(username="owner2", password_hash="x", role="OWNER"))
    s.flush()
    svc.set_user_enabled(s, "owner", False)
    assert client.get("/api/ui/loans").status_code == 401
    r = login(client)
    assert r.status_code == 401 and "Wrong username" in r.json()["detail"]


def test_logout_ends_the_session(client):
    login(client)
    assert client.post("/api/logout", json={}).json()["ok"]
    assert client.get("/api/ui/home").status_code == 401


def test_entry_messages_read_as_plain_words(client):
    """UI review: messages said 'Topup recorded.', 'Rate_Change recorded.' and 'Entry 11 reversed.'"""
    c = client
    login(c)
    pid = c.post("/api/parties", json={"name": "Ravi"}).json()["id"]
    lid = c.post("/api/ui/loans", json={"party_id": pid, "direction": "GIVEN", "book": "PERSONAL", "principal": "1000",
                                        "start_date": "2026-01-01", "rate": "2", "due_day": "1"}).json()["id"]
    r = c.post(f"/api/ui/loans/{lid}/entries", json={"type": "TOPUP", "entry_date": "2026-02-01", "amount": "500"})
    assert r.json()["message"] == "Top-up of ₹500.00 on 01 Feb 2026 recorded."
    r = c.post(f"/api/ui/loans/{lid}/entries", json={"type": "RATE_CHANGE", "entry_date": "2026-03-01", "new_rate": "1.5"})
    assert r.json()["message"] == "Rate change to 1.5% a month on 01 Mar 2026 recorded."
    eid = c.get(f"/api/loans/{lid}/entries").json()[0]["id"]
    r = c.post(f"/api/ui/entries/{eid}/reverse", json={})
    assert r.json()["message"] == f"Rate change on 01 Mar 2026 reversed (entry #{eid})."


def test_person_details_update(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Ravi"}).json()["id"]
    r = client.post(f"/api/parties/{pid}", json={"name": "Ravi Kumar", "phone": "98480 12345", "address": "12 Market St",
                                                 "id_proof_ref": "", "guarantor": "Sita", "notes": "", "risk_tag": "careful"})
    assert r.json()["message"] == "Saved."
    p = client.get(f"/api/ui/parties/{pid}").json()["party"]
    assert (p["name"], p["guarantor"], p["risk_tag"]) == ("Ravi Kumar", "Sita", "careful")
    assert "Person edited" in labels(client)


def test_funding_screen_and_refusal_message(client):
    login(client)
    _, taken = new_loan(client, "Bank", direction="TAKEN", principal_paise=10_000_000)
    _, given = new_loan(client, "Ravi", principal_paise=50_000_000)
    ok = client.post("/api/ui/funding", json={"taken_loan_id": taken, "given_loan_id": given, "amount": "60,000"})
    assert ok.json()["message"] == "Funding link saved."
    page = client.get("/api/ui/funding").json()
    assert page["taken"][0]["unlinked_paise"] == 4_000_000 and page["given"][0]["unfunded_paise"] == 44_000_000
    bad = client.post("/api/ui/funding", json={"taken_loan_id": taken, "given_loan_id": given, "amount": "50,000"})
    assert bad.status_code == 400 and "₹40,000.00 of LN-" in bad.json()["detail"]
