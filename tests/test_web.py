from bhav.db import get_db
from tests.conftest import make_pro, post, signup


def test_landing_page_with_data(demo, client):
    r = client.get("/")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Demo data" in body and "Know the price before you buy" in body
    assert "Wheat" in body and "Your monthly basket" in body


def test_landing_page_empty_database(client):
    r = client.get("/")
    assert r.status_code == 200 and "Prices are being loaded" in r.get_data(as_text=True)


def test_security_headers(client):
    r = client.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Frame-Options"] == "DENY"


def test_post_without_csrf_token_is_rejected(client):
    assert client.post("/subscribe", data={"email": "a@example.com"}).status_code == 400
    client.get("/")  # session now has a token, but a wrong one must still fail
    assert client.post("/subscribe", data={"email": "a@example.com", "_csrf": "wrong"}).status_code == 400


def test_subscribe(app, client):
    assert post(client, "/subscribe", {"email": "Reader@Example.com"}).status_code == 302
    post(client, "/subscribe", {"email": "not-an-email"})
    with app.app_context():
        rows = get_db().execute("SELECT email FROM subscribers").fetchall()
    assert [r["email"] for r in rows] == ["reader@example.com"]


def test_signup_login_logout(client):
    assert signup(client).headers["Location"].endswith("/app")
    assert client.get("/app").status_code == 200
    post(client, "/logout")
    assert client.get("/app").status_code == 302
    bad = post(client, "/login", {"email": "a@example.com", "password": "wrong password"})
    assert "email and password" in bad.get_data(as_text=True)  # apostrophes are HTML-escaped
    ok = post(client, "/login", {"email": "a@example.com", "password": "correct horse"})
    assert ok.status_code == 302


def test_signup_validation(client):
    assert "valid email" in signup(client, email="nope").get_data(as_text=True)
    assert "at least 8" in signup(client, password="short").get_data(as_text=True)
    signup(client)
    post(client, "/logout")
    assert "already exists" in signup(client).get_data(as_text=True)


def test_login_next_cannot_redirect_off_site(client):
    signup(client)
    post(client, "/logout")
    r = post(client, "/login?next=//evil.example", {"email": "a@example.com", "password": "correct horse"})
    assert r.headers["Location"].endswith("/app")
    post(client, "/logout")
    r = post(client, "/login?next=/prices", {"email": "a@example.com", "password": "correct horse"})
    assert r.headers["Location"].endswith("/prices")


def test_commodity_page_free_limits(demo, client):
    assert client.get("/prices/does-not-exist").status_code == 404
    r = client.get("/prices/wheat?days=365")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "History beyond 30 days is part of Pro" in body
    assert "Comparing states" not in body
    assert "Showing the 10 cheapest markets" in body


def test_commodity_page_draws_a_real_chart(demo, client):
    body = client.get("/prices/wheat").get_data(as_text=True)
    assert '<polyline points="0.0,' in body and 'cx=""' not in body and 'cy=""' not in body


def test_commodity_page_pro_sees_more(demo, client):
    signup(client)
    make_pro(demo)
    body = client.get("/prices/wheat?days=90&state=Punjab").get_data(as_text=True)
    assert "Comparing states" in body and "in Punjab" in body
    assert "History beyond 30 days" not in body


def test_commodity_page_ignores_unknown_state(demo, client):
    r = client.get("/prices/wheat?state=Atlantis")
    assert r.status_code == 200 and "Atlantis" not in r.get_data(as_text=True)


def test_prices_index_and_healthz(demo, client):
    assert "Arhar" in client.get("/prices").get_data(as_text=True)
    assert client.get("/healthz").get_json() == {"status": "ok", "latest_price_date": "2026-11-14"}


def test_free_user_cannot_use_pro_features(demo, client):
    signup(client)
    r = post(client, "/app/alerts", {"commodity": "wheat", "direction": "above", "threshold": "30"})
    assert r.status_code == 302
    post(client, "/app/basket", {"commodity": "wheat", "qty_kg": "10"})
    with demo.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"] == 0
        assert db.execute("SELECT COUNT(*) AS n FROM basket_items").fetchone()["n"] == 0


def test_pro_user_alerts_basket_and_purchases(demo, client):
    signup(client)
    make_pro(demo)
    post(client, "/app/alerts", {"commodity": "wheat", "direction": "above", "threshold": "30"})
    post(client, "/app/alerts", {"commodity": "wheat", "direction": "sideways", "threshold": "30"})
    post(client, "/app/alerts", {"commodity": "wheat", "direction": "below", "threshold": "-4"})
    post(client, "/app/basket", {"commodity": "wheat", "qty_kg": "40"})
    post(client, "/app/basket", {"commodity": "wheat", "qty_kg": "50"})  # updates, not duplicates
    post(client, "/app/purchases", {"commodity": "wheat", "bought_on": "2026-11-10",
                                    "price_per_kg": "29", "qty_kg": "20"})
    with demo.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"] == 1
        assert db.execute("SELECT qty_kg FROM basket_items").fetchone()["qty_kg"] == 50
    body = client.get("/app").get_data(as_text=True)
    assert "Monthly total" in body and "above ₹30.0/kg" in body


def test_users_cannot_delete_each_others_rows(demo, client):
    signup(client)
    make_pro(demo)
    post(client, "/app/alerts", {"commodity": "wheat", "direction": "above", "threshold": "30"})
    post(client, "/logout")
    signup(client, "b@example.com")
    make_pro(demo, "b@example.com")
    post(client, "/app/alerts/1/delete")
    with demo.app_context():
        assert get_db().execute("SELECT COUNT(*) AS n FROM alerts").fetchone()["n"] == 1


def test_settings_validation(demo, client):
    signup(client)
    post(client, "/app/settings", {"state": "Atlantis", "telegram_chat_id": "123", "digest_opt_in": "1"})
    post(client, "/app/settings", {"state": "Punjab", "telegram_chat_id": "abc"})  # rejected
    with demo.app_context():
        row = get_db().execute("SELECT state, telegram_chat_id FROM users").fetchone()
    assert (row["state"], row["telegram_chat_id"]) == ("", "123")


def test_pro_request_recorded_once(demo, client):
    signup(client)
    post(client, "/app/upgrade")
    post(client, "/app/upgrade")
    with demo.app_context():
        assert get_db().execute("SELECT COUNT(*) AS n FROM pro_requests").fetchone()["n"] == 1
