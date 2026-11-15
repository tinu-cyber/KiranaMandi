from datetime import date

import pytest

from kiranamandi import create_app
from kiranamandi.db import get_db
from kiranamandi.ingest import seed_demo

TODAY = date(2026, 11, 14)


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "TESTING": True, "SECRET_KEY": "test-secret", "DATABASE": str(tmp_path / "test.sqlite"),
        "BASE_URL": "http://test.local",
    })
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def demo(app):
    """App context with 40 days of demo prices ending 2026-11-14."""
    with app.app_context():
        seed_demo(get_db(), days=40, today=TODAY, seed=1)
        yield app


def post(client, url, data=None, **kwargs):
    """POST with a valid CSRF token."""
    with client.session_transaction() as sess:
        sess.setdefault("_csrf", "test-token")
        token = sess["_csrf"]
    return client.post(url, data={**(data or {}), "_csrf": token}, **kwargs)


def signup(client, email="a@example.com", password="correct horse"):
    return post(client, "/signup", {"email": email, "password": password})


def make_pro(app, email="a@example.com"):
    with app.app_context():
        db = get_db()
        db.execute("UPDATE users SET plan = 'pro' WHERE email = ?", (email,))
        db.commit()
