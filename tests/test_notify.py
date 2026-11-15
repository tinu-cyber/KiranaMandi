from kiranamandi import notify, prices
from kiranamandi.db import get_db
from tests.conftest import make_pro, post, signup


def _add_alert(db, user_id, direction, threshold, state=""):
    db.execute(
        "INSERT INTO alerts (user_id, commodity, state, direction, threshold_per_kg) "
        "VALUES (?, 'wheat', ?, ?, ?)", (user_id, state, direction, threshold))
    db.commit()


def test_alert_fires_once_then_rearms(demo, client, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body: sent.append((to, subject, body)))
    signup(client)
    make_pro(demo)
    with demo.app_context():
        db = get_db()
        price = prices.summary("wheat")["now"]
        _add_alert(db, 1, "above", price * 0.5)
        assert notify.check_alerts() == 1
        assert notify.check_alerts() == 0  # still above: no repeat
        assert len(sent) == 1 and "Wheat" in sent[0][2]
        db.execute("UPDATE alerts SET threshold_per_kg = ?", (price * 2,))
        db.commit()
        assert notify.check_alerts() == 0  # condition false: re-arms silently
        assert db.execute("SELECT triggered FROM alerts").fetchone()["triggered"] == 0
        db.execute("UPDATE alerts SET threshold_per_kg = ?", (price * 0.5,))
        db.commit()
        assert notify.check_alerts() == 1  # crosses again
    assert len(sent) == 2


def test_below_alert_and_state_filter(demo, client, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda *a: sent.append(a))
    signup(client)
    make_pro(demo)
    with demo.app_context():
        price = prices.summary("wheat", state="Punjab")["now"]
        _add_alert(get_db(), 1, "below", price * 2, state="Punjab")
        assert notify.check_alerts() == 1
    assert "in Punjab" in sent[0][2]


def test_free_users_alerts_are_ignored(demo, client, monkeypatch):
    monkeypatch.setattr(notify, "send_email", lambda *a: (_ for _ in ()).throw(AssertionError))
    signup(client)  # stays on the free plan
    with demo.app_context():
        _add_alert(get_db(), 1, "above", 0.01)
        assert notify.check_alerts() == 0


def test_digest_goes_to_users_and_subscribers(demo, client, monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body: sent.append((to, body)))
    signup(client)
    make_pro(demo)
    post(client, "/app/basket", {"commodity": "wheat", "qty_kg": "40"})
    post(client, "/subscribe", {"email": "reader@example.com"})
    with demo.app_context():
        count, sample = notify.send_digest(dry_run=True)
        assert count == 2 and sample and not sent
        count, _ = notify.send_digest()
    assert count == 2
    bodies = dict(sent)
    assert "Your basket" in bodies["a@example.com"]
    assert "Your basket" not in bodies["reader@example.com"]
    assert "Unsubscribe: http://test.local/unsubscribe/" in bodies["reader@example.com"]


def test_digest_skips_when_no_data(app, monkeypatch):
    with app.app_context():
        assert notify.send_digest() == (0, None)


def test_unsubscribe_link_works_and_tampering_fails(demo, client):
    post(client, "/subscribe", {"email": "reader@example.com"})
    with demo.app_context():
        url = notify.unsubscribe_url("reader@example.com")
    token = url.rsplit("/", 1)[1]
    assert client.get(f"/unsubscribe/{token}x").status_code == 404
    assert client.get(f"/unsubscribe/{token}").status_code == 200
    with demo.app_context():
        assert get_db().execute("SELECT COUNT(*) AS n FROM subscribers").fetchone()["n"] == 0


def test_email_not_configured_is_logged_not_raised(app):
    with app.app_context():
        assert notify.send_email("a@example.com", "Hi", "Body") is False
        assert notify.send_telegram("123", "Hi") is False
