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


def login(c, code=None):
    return c.post("/login", data={"username": "owner", "password": "a long password",
                                  "code": code or auth.totp_code(SECRET)}, follow_redirects=False)


def test_pages_need_login(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/api/loans").status_code == 401


def test_wrong_code_rejected(client):
    r = login(client, code="000000")
    assert r.headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 303


def test_full_flow(client):
    assert login(client).headers["location"] == "/"
    r = client.post("/parties", data={"name": "Ravi"}, follow_redirects=False)
    party_id = int(r.headers["location"].rsplit("/", 1)[1])
    r = client.post("/loans", data={"party_id": party_id, "direction": "GIVEN", "book": "PERSONAL",
                                    "principal": "1,00,000", "start_date": "2026-01-01", "rate": "2",
                                    "due_day": "1"}, follow_redirects=False)
    loan_id = int(r.headers["location"].rsplit("/", 1)[1])
    client.post(f"/loans/{loan_id}/entries", data={"type": "PAYMENT", "amount": "1500",
                                                   "entry_date": "2026-02-05"})
    page = client.post("/quick", data={"loan_id": loan_id, "amount": "32,500", "entry_date": "2026-03-15"}).text
    assert "principal ₹30,000.00" in page
    data = client.get(f"/api/loans/{loan_id}").json()
    assert data["balances"]["payoff_today_paise"] == 7_167_097
    assert data["principal_outstanding_paise"] == 7_000_000

    first_payment = client.get(f"/api/loans/{loan_id}/entries").json()[-2]
    assert first_payment["amount_paise"] == 150_000
    client.post(f"/entries/{first_payment['id']}/reverse", data={})
    assert client.get(f"/api/loans/{loan_id}").json()["principal_outstanding_paise"] == 7_150_000

    for url in ["/", "/loans", "/parties", f"/parties/{party_id}", f"/loans/{loan_id}", "/quick",
                "/calendar?days=60", "/reports", f"/reports/statement/{loan_id}", "/funding",
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
    assert "book-personal" in client.get("/").text


def test_validation_errors_are_messages(client):
    login(client)
    pid = client.post("/api/parties", json={"name": "X"}).json()["id"]
    r = client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "",
                                        "principal_paise": 1, "start_date": "2026-01-01", "rate": "2"})
    assert r.status_code == 400 and "book" in r.json()["detail"]
    page = client.post("/loans", data={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                       "principal": "1.005", "rate": "2"}, headers={"referer": "/loans/new"})
    assert "2 decimal places" in page.text


def test_login_rate_limited(client):
    for _ in range(5):
        login(client, code="000000")
    assert "Too many attempts" in client.post("/login", data={"username": "owner", "password": "x"}).text
