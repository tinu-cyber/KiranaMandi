import pytest
import requests

from kiranamandi import ingest
from kiranamandi.db import get_db, get_meta
from tests.conftest import TODAY

GOOD = {"arrival_date": "14/11/2026", "state": "Punjab", "district": "Ludhiana",
        "market": "Ludhiana", "commodity": "Wheat", "variety": "Dara",
        "min_price": "2500", "max_price": "2700", "modal_price": "2600"}


def test_normalise_accepts_good_record():
    row = ingest.normalise(GOOD)
    assert row[0] == "2026-11-14" and row[4] == "wheat" and row[8] == 2600.0


@pytest.mark.parametrize("change", [
    {"modal_price": "0"}, {"modal_price": "NA"}, {"modal_price": ""}, {"arrival_date": "soon"},
    {"market": ""}, {"commodity": ""}, {"state": ""},
])
def test_normalise_rejects_bad_records(change):
    assert ingest.normalise({**GOOD, **change}) is None


def test_upsert_is_idempotent_and_updates(app):
    with app.app_context():
        db = get_db()
        assert ingest.upsert(db, [GOOD, {**GOOD, "modal_price": "bad"}]) == 1
        ingest.upsert(db, [{**GOOD, "modal_price": "2650"}])
        rows = db.execute("SELECT modal_price FROM prices").fetchall()
        assert [r["modal_price"] for r in rows] == [2650.0]
        assert db.execute("SELECT COUNT(*) AS n FROM commodities").fetchone()["n"] == 1


def test_import_csv_with_agmarknet_style_headers(app, tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        "State Name,District Name,Market Name,Variety,Group,Min Price (Rs./Quintal),"
        "Max Price (Rs./Quintal),Modal Price (Rs./Quintal),Reported Date,Commodity\n"
        "Punjab,Ludhiana,Ludhiana,Dara,Cereals,2500,2700,\"2,600\",14 Nov 2026,Wheat\n"
        "Punjab,Ludhiana,Ludhiana,Dara,Cereals,2500,2700,,14 Nov 2026,Wheat\n"
    )
    with app.app_context():
        assert ingest.import_csv(get_db(), str(path)) == 1
        row = get_db().execute("SELECT * FROM prices").fetchone()
        assert row["price_date"] == "2026-11-14" and row["modal_price"] == 2600.0


def test_fetch_paginates(monkeypatch):
    pages = [{"total": 3, "records": [{"a": 1}, {"a": 2}]}, {"total": 3, "records": [{"a": 3}]}]
    calls = []

    def fake_get_json(session, url, params):
        calls.append(params["offset"])
        return pages[len(calls) - 1]

    monkeypatch.setattr(ingest, "_get_json", fake_get_json)
    records = ingest.fetch_agmarknet("key", "rid", page_size=2)
    assert len(records) == 3 and calls == [0, 2]


def test_fetch_needs_api_key():
    with pytest.raises(ingest.IngestError, match="DATA_GOV_API_KEY"):
        ingest.fetch_agmarknet("", "rid")


def test_request_failure_does_not_leak_api_key(monkeypatch):
    def boom(self, url, params=None, timeout=None):
        raise requests.ConnectionError(f"failed for {url}?api-key=SECRET123")

    monkeypatch.setattr(requests.Session, "get", boom)
    monkeypatch.setattr(ingest.time, "sleep", lambda s: None)
    with pytest.raises(ingest.IngestError) as err:
        ingest.fetch_agmarknet("SECRET123", "rid")
    assert "SECRET123" not in str(err.value)


def test_real_data_replaces_demo_data(app, tmp_path):
    with app.app_context():
        db = get_db()
        ingest.seed_demo(db, days=5, today=TODAY)
        assert get_meta("demo_data") == "1"
        path = tmp_path / "real.csv"
        path.write_text("state,market,commodity,date,modal_price\nPunjab,Ludhiana,Wheat,2026-11-14,2600\n")
        ingest.import_csv(db, str(path))
        assert get_meta("demo_data") == "0"
        assert db.execute("SELECT COUNT(*) AS n FROM prices").fetchone()["n"] == 1
        with pytest.raises(ingest.IngestError):
            ingest.seed_demo(db)  # refuses to mix demo rows into real data


def test_demo_seed_is_deterministic(app):
    with app.app_context():
        db = get_db()
        ingest.seed_demo(db, days=10, today=TODAY, seed=3)
        first = db.execute("SELECT SUM(modal_price) AS s, COUNT(*) AS n FROM prices").fetchone()
        ingest.seed_demo(db, days=10, today=TODAY, seed=3)
        second = db.execute("SELECT SUM(modal_price) AS s, COUNT(*) AS n FROM prices").fetchone()
        assert tuple(first) == tuple(second)


def test_fetch_uses_proxy_when_set(monkeypatch):
    seen = {}

    def fake_get_json(session, url, params):
        seen["proxies"] = session.proxies
        return {"records": [], "total": 0}

    monkeypatch.setattr(ingest, "_get_json", fake_get_json)
    ingest.fetch_agmarknet("key", "res", proxy="http://10.0.0.5:8888")
    assert seen["proxies"] == {"http": "http://10.0.0.5:8888", "https": "http://10.0.0.5:8888"}
