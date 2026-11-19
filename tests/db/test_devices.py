"""Android app sign-in: device tokens (bearer), their limits, and CORS for the app's origin."""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from loan_tracker import auth
from loan_tracker import services as svc
from loan_tracker.db import models as m
from tests.db.test_web import SECRET, login, make_client


def _users(s):
    s.add(m.AppUser(username="owner", password_hash=auth.hash_password("a long password"), totp_secret=SECRET))
    s.add(m.AppUser(username="acct", password_hash=auth.hash_password("acct pass word 1"), totp_secret=SECRET,
                    role="VIEWER"))
    s.flush()


@pytest.fixture()
def app_client(s):
    _users(s)
    return make_client(s)


@pytest.fixture()
def multi_client(s):
    """SINGLE_SESSION=0: the same user may be signed in on several phones and browsers."""
    _users(s)
    return make_client(s, single_session=False)


def phone(client, username="owner", password="a long password", name="Pixel 8") -> TestClient:
    """A fresh client with no cookies, signed in the way the Android app does."""
    c = TestClient(client.app)
    r = c.post("/api/device/login", json={"username": username, "password": password,
                                          "code": auth.totp_code(SECRET), "device_name": name})
    assert r.status_code == 201, r.text
    c.headers["Authorization"] = f"Bearer {r.json()['token']}"
    c.device_id = r.json()["device_id"]
    return c


def test_phone_signs_in_and_works_without_cookies_or_csrf(app_client, s):
    p = phone(app_client)
    token = p.headers["Authorization"].split()[1]
    assert token.startswith("ld_") and len(token) > 40
    assert "set-cookie" not in p.post("/api/device/login", json={"username": "x", "password": "y"}).headers
    me = p.get("/api/session").json()
    assert (me["user"], me["role"], me["device_id"]) == ("owner", "OWNER", p.device_id)
    # writes need no CSRF token: the bearer token is not sent by a browser on its own
    pid = p.post("/api/parties", json={"name": "Ravi"}).json()["id"]
    lid = p.post("/api/ui/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                        "principal": "10,000", "start_date": "2026-01-01", "rate": "2"}).json()["id"]
    assert p.get(f"/api/ui/loans/{lid}").json()["brief"]["principal_outstanding_paise"] == 1_000_000
    assert p.get("/export.csv").status_code == 200
    # only the hash is stored
    d = s.get(m.AppDevice, p.device_id)
    assert d.name == "Pixel 8" and d.token_hash != token and token not in d.token_hash
    # audit rows name the user, not "unknown"
    rows = s.scalars(select(m.AuditLog).where(m.AuditLog.action != "LOGIN_FAILED").order_by(m.AuditLog.id)).all()
    assert {r.actor for r in rows} == {"owner"} and [r.action for r in rows] == ["DEVICE_SIGNED_IN", "PARTY_CREATED", "LOAN_CREATED"]


def test_phone_sign_in_checks_password_code_and_limits(app_client):
    c = TestClient(app_client.app)
    r = c.post("/api/device/login", json={"username": "owner", "password": "a long password", "code": "000000"})
    assert r.status_code == 401 and "Wrong username, password or code." in r.json()["detail"]
    for _ in range(4):
        c.post("/api/device/login", json={"username": "owner", "password": "nope"})
    r = c.post("/api/device/login", json={"username": "owner", "password": "a long password",
                                          "code": auth.totp_code(SECRET)})
    assert r.status_code == 429                          # the same limit as the browser login


def test_bad_or_mixed_credentials_are_refused(app_client):
    for bad in ("Bearer nope", "Bearer ld_" + "x" * 43, "Basic b3duZXI6cGFzcw==", "Bearer "):
        c = TestClient(app_client.app)
        c.headers["Authorization"] = bad
        assert c.get("/api/ui/home").status_code == 401, bad
    # a bad token is not rescued by a good cookie
    login(app_client)
    assert app_client.get("/api/ui/home").status_code == 200
    assert app_client.get("/api/ui/home", headers={"Authorization": "Bearer ld_wrong"}).status_code == 401


def test_sign_out_password_reset_disable_and_idle_end_a_phone(multi_client, s):
    app_client = multi_client
    # signed out from the browser by the owner
    p1 = phone(app_client, name="Old phone")
    login(app_client)
    devices = app_client.get("/api/devices").json()
    assert [d["name"] for d in devices] == ["Old phone"] and devices[0]["live"]
    msg = app_client.post(f"/api/devices/{p1.device_id}/revoke", json={}).json()["message"]
    assert "Old phone is signed out" in msg
    assert p1.get("/api/ui/home").status_code == 401
    assert app_client.post(f"/api/devices/{p1.device_id}/revoke", json={}).status_code == 400
    # the phone signs itself out
    p2 = phone(app_client)
    assert p2.post("/api/device/logout", json={}).json()["ok"]
    assert p2.get("/api/ui/home").status_code == 401
    # a password reset ends every phone
    p3 = phone(app_client)
    owner = s.scalar(select(m.AppUser).where(m.AppUser.username == "owner"))
    owner.session_epoch += 1
    s.flush()
    assert p3.get("/api/ui/home").status_code == 401
    # unused for longer than APP_IDLE_MINUTES (30): signed out, and told why
    p4 = phone(app_client)
    s.execute(text("UPDATE app_device SET last_used_at = now() - interval '31 minutes' WHERE id = :i"), {"i": p4.device_id})
    s.expire_all()
    r = p4.get("/api/ui/home")
    assert r.status_code == 401 and r.json()["reason"] == "idle" and "not used for a while" in r.json()["detail"]
    assert p4.get("/api/session").json()["user"] is None
    assert p4.get("/api/ui/home").status_code == 401                     # stays refused
    assert svc.resolve_device_state(s.get(m.AppDevice, p4.device_id), datetime.now(timezone.utc),
                                    timedelta(minutes=30)) == "IDLE"
    # a disabled user
    p5 = phone(app_client)
    s.add(m.AppUser(username="owner2", password_hash="x", role="OWNER"))
    s.flush()
    svc.set_user_enabled(s, "owner", False)
    assert p5.get("/api/ui/home").status_code == 401
    states = {d.id: svc.resolve_device_state(d, datetime.now(timezone.utc)) for d in svc.list_devices(s)}
    assert states[p1.device_id] == "SIGNED_OUT" and states[p3.device_id] == "ENDED" and states[p5.device_id] == "ENDED"


def test_viewer_phone_is_read_only(app_client):
    login(app_client)
    pid = app_client.post("/api/parties", json={"name": "Ravi"}).json()["id"]
    lid = app_client.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "BUSINESS",
                                              "principal_paise": 1_000_000, "start_date": "2026-01-01",
                                              "rate": "2"}).json()["id"]
    v = phone(app_client, "acct", "acct pass word 1", "Accountant phone")
    assert v.get("/api/session").json()["role"] == "VIEWER"
    assert v.get(f"/api/ui/loans/{lid}").status_code == 200
    r = v.post(f"/api/ui/loans/{lid}/entries", json={"type": "PAYMENT", "amount": "1"})
    assert r.status_code == 403 and "Read-only access" in r.json()["detail"]
    assert v.post("/api/ui/simulate", json={"mode": "loan", "loan_id": lid, "kind": "PAYOFF",
                                            "on": "2026-05-01"}).status_code == 200
    assert v.get("/api/ui/documents").status_code == 403
    owner_phone = phone(app_client)
    mine = v.get("/api/devices").json()
    assert [d["name"] for d in mine] == ["Accountant phone"] and mine[0]["current"]       # only their own
    assert v.post(f"/api/devices/{owner_phone.device_id}/revoke", json={}).status_code == 403
    assert v.post("/api/device/logout", json={}).json()["ok"]                             # may sign itself out
    assert owner_phone.get("/api/ui/home").status_code == 200


def test_device_rows_are_guarded_in_the_database(app_client, s):
    p = phone(app_client)
    with pytest.raises(Exception, match="signed-in phones are kept"):
        with s.begin_nested():
            s.execute(text("DELETE FROM app_device WHERE id = :i"), {"i": p.device_id})
    with pytest.raises(Exception, match="only be signed out once"):
        with s.begin_nested():
            s.execute(text("UPDATE app_device SET name = 'x' WHERE id = :i"), {"i": p.device_id})


def test_cors_allows_only_the_app(app_client):
    c = TestClient(app_client.app)
    ok = c.options("/api/ui/home", headers={"Origin": "https://localhost", "Access-Control-Request-Method": "GET",
                                            "Access-Control-Request-Headers": "authorization"})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "https://localhost"
    assert "authorization" in ok.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in ok.headers           # never cookies across origins
    bad = c.options("/api/ui/home", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET",
                                             "Access-Control-Request-Headers": "authorization"})
    assert "access-control-allow-origin" not in bad.headers
    p = phone(app_client)
    r = p.get("/api/ui/home", headers={"Origin": "https://localhost"})
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "https://localhost"


def test_cookie_requests_still_need_the_csrf_token(app_client):
    login(app_client)
    token = app_client.headers.pop("X-CSRF-Token")
    assert app_client.post("/api/parties", json={"name": "Sneaky"}).status_code == 403
    app_client.headers["X-CSRF-Token"] = token
    assert app_client.post("/api/device/logout", json={}).status_code == 400   # a browser is not a phone


def test_cli_lists_and_signs_out_phones(app_client, s, capsys, monkeypatch):
    from contextlib import contextmanager
    from loan_tracker import cli
    p = phone(app_client, name="Lost phone")

    @contextmanager
    def same_session():
        yield s
    monkeypatch.setattr(cli, "session_scope", same_session)
    cli.cmd_devices(type("A", (), {"cmd": "list-devices"})())
    assert "Lost phone" in capsys.readouterr().out
    cli.cmd_devices(type("A", (), {"cmd": "revoke-device", "device_id": p.device_id})())
    assert "is signed out" in capsys.readouterr().out
    assert p.get("/api/ui/home").status_code == 401


def test_phone_chooses_the_book_with_a_header(app_client):
    p = phone(app_client)
    pid = p.post("/api/parties", json={"name": "Ravi"}).json()["id"]
    p.post("/api/loans", json={"party_id": pid, "direction": "GIVEN", "book": "PERSONAL", "principal_paise": 100_000,
                               "start_date": "2026-01-01", "rate": "2"})
    assert len(p.get("/api/ui/loans").json()) == 1
    assert p.get("/api/ui/loans", headers={"X-Book": "business"}).json() == []
    assert p.get("/api/session", headers={"X-Book": "personal"}).json()["book"] == "personal"
    pre = TestClient(app_client.app).options("/api/ui/loans", headers={
        "Origin": "https://localhost", "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-book"})
    assert "x-book" in pre.headers["access-control-allow-headers"].lower()


def test_one_sign_in_per_user(app_client):
    """SINGLE_SESSION (default): a new sign-in, on a phone or in a browser, ends the others."""
    p1 = phone(app_client, name="Phone one")
    assert p1.get("/api/ui/home").status_code == 200
    p2 = phone(app_client, name="Phone two")
    r = p1.get("/api/ui/home")
    assert r.status_code == 401 and r.json()["reason"] == "elsewhere" and "signed in somewhere else" in r.json()["detail"]
    assert p2.get("/api/ui/home").status_code == 200
    login(app_client)                                   # now the browser
    assert p2.get("/api/ui/home").status_code == 401
    assert app_client.get("/api/ui/home").status_code == 200
    b2 = TestClient(app_client.app)                     # a second browser
    login(b2)
    assert "signed in somewhere else" in app_client.get("/api/session").json()["ended"]
    assert app_client.get("/api/ui/home").status_code == 401
    assert b2.get("/api/ui/home").status_code == 200
    # other users are not affected
    v = phone(app_client, "acct", "acct pass word 1")
    assert b2.get("/api/ui/home").status_code == 200 and v.get("/api/ui/home").status_code == 200
    labels = [d["state"] for d in b2.get("/api/devices").json()]
    assert labels.count("ENDED") == 2


def test_browser_session_ends_when_idle_or_too_old(app_client, monkeypatch):
    import time as _t
    login(app_client)
    assert app_client.get("/api/session").json()["idle_minutes"] == 30
    real = _t.time
    monkeypatch.setattr(_t, "time", lambda: real() + 29 * 60)            # 29 minutes later: still fine
    assert app_client.get("/api/ui/home").status_code == 200
    monkeypatch.setattr(_t, "time", lambda: real() + 29 * 60 + 31 * 60)  # 31 minutes after that: signed out
    r = app_client.get("/api/ui/home")
    assert r.status_code == 401 and r.json()["reason"] == "idle"
    monkeypatch.setattr(_t, "time", real)
    login(app_client)
    # kept busy, but never longer than SESSION_MAX_HOURS (12)
    for minutes in range(20, 12 * 60, 20):
        monkeypatch.setattr(_t, "time", lambda m=minutes: real() + m * 60)
        assert app_client.get("/api/ui/home").status_code == 200, minutes
    monkeypatch.setattr(_t, "time", lambda: real() + 12 * 3600 + 60)
    assert app_client.get("/api/ui/home").status_code == 401


def test_idle_limits_are_settings(s):
    _users(s)
    c = make_client(s, session_idle_minutes=10, app_idle_minutes=60)
    login(c)
    assert c.get("/api/session").json()["idle_minutes"] == 10
    assert phone(c).get("/api/session").json()["idle_minutes"] == 60
    from loan_tracker.config import _bounded_int
    import os
    os.environ["SESSION_IDLE_MINUTES"] = "0"
    try:
        assert _bounded_int("SESSION_IDLE_MINUTES", 30, 5, 1440) == 5           # cannot be switched off
    finally:
        del os.environ["SESSION_IDLE_MINUTES"]
