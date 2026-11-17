"""Web app end to end: login with 2FA, create a loan, pay, reverse, every page renders."""
import re
from datetime import date as D

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from loan_tracker import auth
from loan_tracker.config import Settings
from loan_tracker.db import models as m
from loan_tracker.web.app import create_app

SECRET = auth.new_totp_secret()


@pytest.fixture()
def client(s):
    s.add(m.AppUser(username="owner", password_hash=auth.hash_password("a long password"), totp_secret=SECRET))
    s.flush()
    maker = sessionmaker(bind=s.connection(), join_transaction_mode="create_savepoint", expire_on_commit=False)
    app = create_app(Settings(secret_key="test"), maker=maker, today_fn=lambda: D(2026, 4, 1))
    return TestClient(app)


def tok(c, page="/login"):
    return re.search(r'name="csrf_token" value="([^"]+)"', c.get(page).text).group(1)


def post(c, url, data, page="/parties", **kw):
    return c.post(url, data={**data, "csrf_token": tok(c, page)}, **kw)


def login(c, code=None):
    return c.post("/login", data={"username": "owner", "password": "a long password",
                                  "code": code or auth.totp_code(SECRET), "csrf_token": tok(c)},
                  follow_redirects=False)


def test_pages_need_login(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/api/loans").status_code == 401


def test_wrong_code_rejected(client):
    r = login(client, code="000000")
    assert r.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_full_flow(client):
    assert login(client).headers["location"] == "/"
    r = post(client, "/parties", {"name": "Ravi"}, follow_redirects=False)
    party_id = int(r.headers["location"].rsplit("/", 1)[1])
    r = post(client, "/loans", {"party_id": party_id, "direction": "GIVEN", "book": "PERSONAL",
                                "principal": "1,00,000", "start_date": "2026-01-01", "rate": "2",
                                "due_day": "1"}, follow_redirects=False)
    loan_id = int(r.headers["location"].rsplit("/", 1)[1])
    post(client, f"/loans/{loan_id}/entries", {"type": "PAYMENT", "amount": "1500", "entry_date": "2026-02-05"})
    page = post(client, "/quick", {"loan_id": loan_id, "amount": "32,500", "entry_date": "2026-03-15"}).text
    assert "principal ₹30,000.00" in page
    data = client.get(f"/api/loans/{loan_id}").json()
    assert data["balances"]["payoff_today_paise"] == 7_167_097
    assert data["principal_outstanding_paise"] == 7_000_000

    first_payment = client.get(f"/api/loans/{loan_id}/entries").json()[-2]
    assert first_payment["amount_paise"] == 150_000
    post(client, f"/entries/{first_payment['id']}/reverse", {})
    assert client.get(f"/api/loans/{loan_id}").json()["principal_outstanding_paise"] == 7_150_000

    for url in ["/", "/loans", "/parties", f"/parties/{party_id}", f"/loans/{loan_id}", "/quick",
                "/calendar?days=60", "/calendar?days=90", "/reports", "/loans?direction=TAKEN&q=ra", f"/reports/statement/{loan_id}", "/funding",
                "/loans/new?direction=TAKEN", "/api/dashboard", "/api/calendar", "/api/reports/fy-interest",
                "/api/reports/overdue", "/api/reminders", "/api/parties", "/healthz"]:
        assert client.get(url).status_code == 200, url
    csv = client.get("/export.csv")
    assert csv.headers["content-type"].startswith("text/csv")


def test_book_toggle_filters(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Suresh"}).json()["id"]
    client.post("/api/loans", json={"party_id": pid, "direction": "TAKEN", "book": "BUSINESS",
                                    "principal_paise": 10_000_000, "start_date": "2026-01-01", "rate": "1.5"})
    assert len(client.get("/api/loans?book=business").json()) == 1
    assert client.get("/api/loans?book=personal").json() == []
    client.get("/book/personal")
    assert 'href="/book/personal" class="selected"' in client.get("/").text


def test_validation_errors_are_messages(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "X"}).json()["id"]
    r = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "",
                                        "principal_paise": 1, "start_date": "2026-01-01", "rate": "2"})
    assert r.status_code == 400 and "book" in r.json()["detail"]
    page = post(client, "/loans", {"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                   "principal": "1.005", "rate": "2"}, headers={"referer": "/loans/new"})
    assert "2 decimal places" in page.text


def test_login_rate_limited(client):
    for _ in range(5):
        login(client, code="000000")
    r = client.post("/login", data={"username": "owner", "password": "x", "csrf_token": tok(client)})
    assert "Too many attempts" in r.text


def test_form_without_csrf_token_is_rejected(client):
    login(client)
    r = client.post("/parties", data={"name": "Sneaky"}, headers={"referer": "/parties"})
    assert "expired" in r.text
    assert client.get("/api/parties").json() == []


def test_redirects_stay_on_site(client):
    login(client)
    r = client.get("/book/business", headers={"referer": "https://evil.example/phish"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    r = client.get("/book/business", headers={"referer": "http://testserver/loans?status=ACTIVE"},
                   follow_redirects=False)
    assert r.headers["location"] == "/loans?status=ACTIVE"


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
    pid = client.post("/api/parties", json={"name": "E"}).json()["id"]
    lid = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                          "principal_paise": 100_000, "start_date": "2026-01-01",
                                          "rate": "2"}).json()["id"]
    assert client.post(f"/api/loans/{lid}/entries", json={"type": "DISBURSE", "amount_paise": 5}).status_code == 400
    assert client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 5.0}).status_code == 400
    ok = client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 500,
                                                        "entry_date": "2026-01-20", "payment_mode": "UPI"})
    assert ok.status_code == 201
    assert client.post("/api/funding", json={"taken_loan_id": lid, "given_loan_id": lid}).status_code == 400
    assert client.post(f"/api/entries/{ok.json()['id']}/reverse", json={"note": "typo"}).status_code == 201


def test_html_form_missing_field_shows_message(client):
    login(client)
    page = post(client, "/funding", {"amount": "5"}, headers={"referer": "/funding"})
    assert "Please check the form" in page.text


def test_audit_log_records_changes_and_logins(client):
    login(client, code="000000")                       # one failed attempt first
    login(client)
    pid = client.post("/api/parties", json={"name": "Audit"}).json()["id"]
    lid = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                          "principal_paise": 100_000, "start_date": "2026-01-01",
                                          "rate": "2"}).json()["id"]
    eid = post(client, f"/loans/{lid}/entries", {"type": "PAYMENT", "amount": "100",
                                                  "entry_date": "2026-01-20"},
               headers={"referer": f"/loans/{lid}"}) and client.get(f"/api/loans/{lid}/entries").json()[0]["id"]
    post(client, f"/entries/{eid}/reverse", {"note": "wrong loan"}, headers={"referer": f"/loans/{lid}"})
    post(client, f"/loans/{lid}/status", {"status": "SETTLED"}, headers={"referer": f"/loans/{lid}"})
    page = client.get("/activity").text
    for label in ["Failed login", "Logged in", "Person added", "Loan created", "Entry recorded",
                  "Entry reversed", "Status changed", "“wrong loan”", "SETTLED"]:
        assert label in page, label
    assert "a long password" not in page
    history = client.get(f"/activity?loan_id={lid}").text
    assert "Entry reversed" in history and "Person added" not in history


def test_failed_change_leaves_no_audit_row(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Nope"}).json()["id"]
    client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                    "principal_paise": 100, "start_date": "2026-05-01", "rate": "2"})
    assert "Loan created" not in client.get("/activity").text


def test_collections_pages_and_api(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Ravi Kumar", "phone": "98480 12345"}).json()["id"]
    lid = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                          "principal_paise": 10_000_000, "start_date": "2026-01-01",
                                          "rate": "2", "due_day": 1}).json()["id"]
    page = client.get(f"/loans/{lid}").text
    assert "Log a contact" in page and "https://wa.me/919848012345?text=Hello%20Ravi" in page
    r = post(client, f"/loans/{lid}/contacts", {"kind": "CALL", "outcome": "PROMISED", "note": "will pay",
                                                 "promise_amount": "2,000", "promise_date": "2026-04-05",
                                                 "contact_date": "2026-03-30"},
             headers={"referer": f"/loans/{lid}"})
    assert "is tracked against real payments" in r.text and "Pending" in r.text
    assert "Promises due soon" in client.get("/collections").text
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
    log = client.get("/activity").text
    assert "Contact logged" in log and "Promise made" in log and "Promise cancelled" in log


PUBLIC = {"/healthz", "/manifest.webmanifest", "/sw.js", "/login", "/logout", "/book/{name}"}


def test_every_route_needs_login(client):
    """Regression guard: a new page or API route that forgets Depends(user) fails here."""
    checked = 0
    for route in client.app.routes:
        path = getattr(route, "path", "")
        if path in PUBLIC or path.startswith("/static") or not getattr(route, "methods", None):
            continue
        url = re.sub(r"\{[^}]+\}", "1", path)
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = client.request(method, url, follow_redirects=False, json={} if path.startswith("/api") else None)
            assert r.status_code in (401, 303, 302, 403), f"{method} {path} -> {r.status_code}"
            if r.status_code in (302, 303):
                assert "/login" in r.headers["location"] or method == "POST", f"{method} {path}"
            checked += 1
    assert checked > 40


def test_api_docs_after_login(client):
    login(client)
    assert "/api/loans" in client.get("/api/openapi.json").json()["paths"]
    assert client.get("/api/docs").status_code == 200


def test_receipts_and_certificate_pages(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Ravi Kumar", "phone": "98480 12345"}).json()["id"]
    lid = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                          "principal_paise": 10_000_000, "start_date": "2026-01-01",
                                          "rate": "2", "due_day": 1, "collateral": "gold chain"}).json()["id"]
    r = post(client, f"/loans/{lid}/entries", {"type": "PAYMENT", "amount": "2,000", "entry_date": "2026-02-01",
                                                "payment_mode": "UPI", "reference": "UPI123"},
             headers={"referer": f"/loans/{lid}"})
    assert "Receipt RCPT-2026-0001 issued." in r.text and "RCPT-2026-0001" in r.text
    rid = client.get("/api/receipts").json()[0]["id"]
    page = client.get(f"/receipts/{rid}").text
    assert "Ravi Kumar" in page and "Rupees Two Thousand Only" in page and "UPI123" in page
    assert "https://wa.me/919848012345?text=Receipt%20RCPT-2026-0001" in page
    assert "RECEIPT_NAME" in page                                  # hint while no issuer name is set
    assert client.get(f"/api/receipts/{rid}").json()["amount_in_words"] == "Rupees Two Thousand Only"
    assert "RCPT-2026-0001" in client.get("/receipts?q=ravi").text
    assert "No receipts yet" in client.get("/receipts?q=nobody").text

    # not closed yet -> no certificate
    assert "not closed yet" in client.get(f"/loans/{lid}/closure", follow_redirects=True).text
    # pay off: principal + Feb interest (1 Feb paid) + Mar 1 due + open period to 1 Apr... use payoff
    payoff = client.get(f"/api/loans/{lid}").json()["balances"]["payoff_today_paise"]
    client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": payoff,
                                                   "entry_date": "2026-04-01"})
    cert = client.get(f"/loans/{lid}/closure").text
    assert "No-dues certificate" in cert and "nothing is due" in cert and "NDC-LN-" in cert
    assert "gold chain" in cert and "Receipts issued</span><b>2</b>" in cert
    assert "No-dues certificate" in client.get(f"/loans/{lid}").text

    # reversal keeps the receipt, marked cancelled, and the certificate goes away
    eid = client.get("/api/receipts").json()[0]["ledger_entry_id"]
    client.post(f"/api/entries/{eid}/reverse", json={"note": "bounced"})
    latest = client.get("/api/receipts").json()[0]
    assert latest["cancelled"] and "bounced" in latest["cancel_reason"]
    assert "CANCELLED" in client.get(f"/receipts/{latest['id']}").text
    assert "not closed yet" in client.get(f"/loans/{lid}/closure", follow_redirects=True).text


def test_receipt_issuer_name_from_settings(s):
    s.add(m.AppUser(username="owner", password_hash=auth.hash_password("a long password"), totp_secret=SECRET))
    s.flush()
    maker = sessionmaker(bind=s.connection(), join_transaction_mode="create_savepoint", expire_on_commit=False)
    settings = Settings(secret_key="test", receipt_name="A. Owner", receipt_business_name="Example Finance",
                        receipt_details="Main Road · 98480 00000")
    c = TestClient(create_app(settings, maker=maker, today_fn=lambda: D(2026, 4, 1)))
    login(c)
    pid = c.post("/api/parties", json={"name": "P"}).json()["id"]
    for book in ("BUSINESS", "PERSONAL"):
        lid = c.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": book,
                                         "principal_paise": 100_000, "start_date": "2026-01-01",
                                         "rate": "2"}).json()["id"]
        c.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 500})
    biz, per = sorted(c.get("/api/receipts").json(), key=lambda r: r["number"])
    assert "Example Finance" in c.get(f"/receipts/{biz['id']}").text
    page = c.get(f"/receipts/{per['id']}").text
    assert "A. Owner" in page and "Main Road · 98480 00000" in page and "RECEIPT_NAME" not in page


def test_cash_pages_and_api(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Suresh"}).json()["id"]
    client.post("/api/loans", json={"party_id": pid, "direction": "TAKEN", "book": "BUSINESS",
                                    "principal_paise": 10_000_000, "start_date": "2026-03-05", "rate": "1.5",
                                    "due_day": 5})
    page = client.get("/cash").text
    assert "Add your bank and cash accounts below" in page
    r = post(client, "/cash/accounts", {"name": "SBI", "kind": "BANK", "book": "BUSINESS", "balance": "1,000"},
             page="/cash")
    assert "Account SBI added." in r.text
    data = client.get("/api/cash").json()
    assert data["opening_paise"] == 100_000 and data["accounts"][0]["name"] == "SBI"
    # Suresh's Rs 1,500 on 5 Apr is more than the Rs 1,000 in the bank
    assert (data["expected"]["short_date"], data["expected"]["short_paise"]) == ("2026-04-05", 50_000)
    assert "Cash: you may be ₹500.00 short on 05 Apr 2026" in client.get("/cash").text
    assert "Cash: you may be ₹500.00 short" in client.get("/").text          # dashboard warning
    acc = data["accounts"][0]["id"]
    post(client, f"/cash/accounts/{acc}/balance", {"balance": "-2,000", "on": "2026-04-01"}, page="/cash")
    assert client.get("/api/cash").json()["opening_paise"] == -200_000
    r = post(client, "/cash/planned", {"description": "FD maturing", "amount": "5,000", "item_date": "2026-04-03",
                                       "direction": "IN", "book": "BUSINESS"}, page="/cash")
    assert "FD maturing" in r.text
    assert client.get("/api/cash").json()["expected"]["short_date"] == "2026-04-01"   # overdrawn today
    cal = client.get("/calendar").text
    assert "FD maturing" in cal and "Planned · Business" in cal and "Starts from" in cal
    # API with typed bodies
    bad = client.post("/api/cash/planned", json={"book": "BUSINESS", "item_date": "2026-04-03",
                                                 "direction": "SIDEWAYS", "amount_paise": 1, "description": "x"})
    assert bad.status_code == 400 and "direction" in bad.json()["detail"]
    pl = client.post("/api/cash/planned", json={"book": "BUSINESS", "item_date": "2026-04-03", "direction": "OUT",
                                                "amount_paise": 100, "description": "tea"}).json()["id"]
    assert client.post(f"/api/cash/planned/{pl}/cancel", json={}).json()["cancelled"]
    assert client.post(f"/api/cash/accounts/{acc}/balance", json={"balance_paise": 5}).status_code == 201
    log = client.get("/activity").text
    assert "Account added" in log and "Balance updated" in log and "Plan added" in log and "Plan cancelled" in log


def test_simulator_page_and_api(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Ravi"}).json()["id"]
    lid = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                          "principal_paise": 10_000_000, "start_date": "2026-04-01", "rate": "2",
                                          "due_day": 1}).json()["id"]
    assert "What if…" in client.get(f"/loans/{lid}").text
    page = client.get(f"/simulate?mode=loan&run=1&loan_id={lid}&kind=EXTRA_PAYMENT&on=2026-05-01"
                      f"&amount=50%2C000&horizon=6").text
    assert "nothing here is saved" in page and "Interest you earn" in page
    assert "−₹5,000.00" in page        # Jun-Oct dues on half the principal: Rs 1,000 less each
    entries_before = len(client.get(f"/api/loans/{lid}/entries").json())
    r = client.post("/api/simulate/loan", json={"loan_id": lid, "kind": "EXTRA_PAYMENT", "on": "2026-05-01",
                                                "amount_paise": 5_000_000, "horizon_months": 6}).json()
    assert r["saved"] is False and r["actual"]["interest_paise"] - r["simulated"]["interest_paise"] == 500_000
    assert len(client.get(f"/api/loans/{lid}/entries").json()) == entries_before
    bad = client.get(f"/simulate?mode=loan&run=1&loan_id={lid}&kind=TOPUP&on=2026-05-01").text
    assert "the amount must be more than zero" in bad
    assert client.post("/api/simulate/loan", json={"loan_id": lid, "kind": "LATER"}).status_code == 400
    new = client.get("/simulate?mode=new&run=1&direction=GIVEN&amount=1%2C00%2C000&rate=2&start=2026-04-15"
                     "&due_day=1&loan_book=PERSONAL&days=60").text
    assert '<a href="/book/all" class="selected"' in new      # the top-bar book switch is untouched
    assert "Interest a month" in new and "₹2,000.00" in new and "No cash accounts yet" in new
    api = client.post("/api/simulate/new-loan", json={"direction": "GIVEN", "book": "BUSINESS",
                                                      "principal_paise": 10_000_000, "rate": "2",
                                                      "start_date": "2026-04-15", "due_day": 1}).json()
    assert api["flows"][0] == {"date": "2026-04-15", "amount_paise": -10_000_000}
    assert api["after"]["expected"]["short_date"] == "2026-04-15"



def test_simulator_explains_arrears_first(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Late"}).json()["id"]
    lid = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                          "principal_paise": 10_000_000, "start_date": "2026-01-01", "rate": "2",
                                          "due_day": 1}).json()["id"]
    page = client.get(f"/simulate?mode=loan&run=1&loan_id={lid}&kind=EXTRA_PAYMENT&on=2026-05-01&amount=50000").text
    assert "₹6,000.00 of interest is unpaid today" in page



def test_api_posts_must_be_json(client):
    """A cross-site HTML form can only send form or text bodies; the API refuses them."""
    login(client)
    pid = client.post("/api/parties", json={"name": "J"}).json()["id"]
    lid = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                          "principal_paise": 100_000, "start_date": "2026-01-01",
                                          "rate": "2"}).json()["id"]
    eid = client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 100}).json()["id"]
    for body in ({"data": {"note": "x"}}, {"content": "note=x", "headers": {"content-type": "text/plain"}}, {}):
        r = client.post(f"/api/entries/{eid}/reverse", **body)
        assert r.status_code == 415, body
    assert client.post(f"/api/entries/{eid}/reverse", json={}).status_code == 201


STATEMENT = b"""Date,Narration,Chq./Ref.No.,Withdrawal Amt.,Deposit Amt.,Closing Balance
05/02/26,UPI-RAVI KUMAR-603612345,603612345,,2000.00,26500.00
06/02/26,UPI-RAVI KUMAR-603612999,603612999,,1000.00,27500.00
07/02/26,SWIGGY ORDER,,350.00,,27150.00
"""


def test_bank_import_flow(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "Ravi Kumar"}).json()["id"]
    lid = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "PERSONAL",
                                          "principal_paise": 10_000_000, "start_date": "2026-01-01", "rate": "2",
                                          "due_day": 1}).json()["id"]
    first = client.post(f"/api/loans/{lid}/entries", json={"type": "PAYMENT", "amount_paise": 200_000,
                                                          "entry_date": "2026-02-04"}).json()["id"]
    assert "Add a bank account" in client.get("/bank").text
    post(client, "/cash/accounts", {"name": "HDFC", "kind": "BANK", "book": "PERSONAL", "balance": "0"}, page="/cash")
    acc = client.get("/api/cash").json()["accounts"][0]["id"]
    r = client.post("/bank/upload", data={"account_id": str(acc), "update_balance": "1", "csrf_token": tok(client, "/bank")},
                    files={"file": ("hdfc-feb.csv", STATEMENT, "text/csv")})
    # the account was opened today (1 Apr) with a balance, so a February statement does not overwrite it
    assert "3 new transaction(s) imported. Balance not changed: you have a newer one (01 Apr 2026)." in r.text
    assert "Review bank lines" in r.text and "Link 1 already recorded" in r.text
    assert "Already recorded" in r.text and "name &#39;ravi&#39; in the narration" in r.text
    imp = client.get("/api/bank/imports").json()[0]["id"]
    # no token, no upload
    bad = client.post("/bank/upload", data={"account_id": str(acc)}, files={"file": ("x.csv", STATEMENT, "text/csv")})
    assert "This form expired" in bad.text
    # link the already-recorded Rs 2,000
    r = post(client, f"/bank/imports/{imp}/link-exact", {}, page=f"/bank/imports/{imp}")
    assert "1 bank line(s) linked" in r.text
    lines = {t["amount_paise"]: t for t in client.get(f"/api/bank/imports/{imp}").json()}
    assert lines[200_000]["status"] == "LINKED" and lines[200_000]["ledger_entry_id"] == first
    # record the Rs 1,000 as a payment: receipt issued
    t2 = lines[100_000]
    assert t2["suggestions"][0]["loan_id"] == lid
    r = post(client, f"/bank/txns/{t2['id']}/record", {"loan_id": str(lid)}, page=f"/bank/imports/{imp}",
             headers={"referer": f"/bank/imports/{imp}"})
    assert "Payment of ₹1,000.00 recorded on LN-" in r.text and "receipt RCPT-2026-" in r.text
    # ignore the Swiggy order, then undo it through the API
    t3 = lines[-35_000]
    post(client, f"/bank/txns/{t3['id']}/ignore", {"note": "food"}, page=f"/bank/imports/{imp}")
    assert client.post(f"/api/bank/txns/{t3['id']}/undo", json={}).json()["status"] == "NEW"
    assert client.post(f"/api/bank/txns/{t3['id']}/record", json={"loan_id": lid}).status_code == 400  # money out
    assert client.post(f"/api/bank/txns/{t3['id']}/ignore", json={"note": "food"}).json()["status"] == "IGNORED"
    page = client.get(f"/bank/imports/{imp}").text
    assert "Nothing left to review" in page
    assert "Linked to entry #" in client.get(f"/bank/imports/{imp}?show=all").text
    # re-upload: nothing new
    r = client.post("/bank/upload", data={"account_id": str(acc), "csrf_token": tok(client, "/bank")},
                    files={"file": ("hdfc-feb.csv", STATEMENT, "text/csv")})
    assert "0 new transaction(s) imported; 3 already imported earlier were skipped." in r.text
    log = client.get("/activity").text
    assert "Statement imported" in log and "Bank line linked" in log and "Bank line ignored" in log


def test_bank_upload_bad_file_shows_message(client):
    login(client)
    post(client, "/cash/accounts", {"name": "HDFC", "kind": "BANK", "book": "PERSONAL", "balance": "0"}, page="/cash")
    acc = client.get("/api/cash").json()["accounts"][0]["id"]
    r = client.post("/bank/upload", data={"account_id": str(acc), "csrf_token": tok(client, "/bank")},
                    files={"file": ("photo.csv", b"\x89PNG\r\n\x1a\n...", "text/csv")}, headers={"referer": "/bank"})
    assert "could not read the statement" in r.text
