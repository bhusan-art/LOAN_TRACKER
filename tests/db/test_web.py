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
