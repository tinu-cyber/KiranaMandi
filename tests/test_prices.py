from bhav import prices
from bhav.db import get_db
from bhav.formatting import inr, pct
from bhav.ingest import upsert


def rec(day, modal, market="M1", state="S1", commodity="Wheat"):
    return {"date": day, "state": state, "market": market, "commodity": commodity,
            "modal_price": modal, "min_price": modal - 100, "max_price": modal + 100}


def test_slugify():
    assert prices.slugify("Arhar (Tur/Red Gram)(Whole)") == "arhar-tur-red-gram-whole"
    assert prices.slugify("!!!") == "item"


def test_inr_uses_indian_grouping():
    assert inr(1234567) == "₹12,34,567"
    assert inr(999) == "₹999"
    assert inr(-1500) == "−₹1,500"
    assert inr(41.256, 1) == "₹41.3"
    assert inr(None) == "–"


def test_pct():
    assert pct(3.14) == "+3.1%"
    assert pct(-2.04) == "−2.0%"
    assert pct(0.01) == "0.0%"
    assert pct(None) == "–"


def test_chart_geometry():
    assert prices.chart_geometry([]) is None
    one = prices.chart_geometry([5.0])
    assert one["line"] == "" and one["last_x"] == "300.0"
    flat = prices.chart_geometry([5.0, 5.0, 5.0])
    assert flat["line"].count(",") == 3  # flat series must not divide by zero
    up = prices.chart_geometry([1.0, 2.0, 3.0])
    ys = [float(p.split(",")[1]) for p in up["line"].split()]
    assert ys[0] > ys[1] > ys[2]  # higher price means smaller y (higher on screen)


def test_summary_change_and_units(app):
    with app.app_context():
        upsert(get_db(), [rec("2026-10-15", 4000), rec("2026-11-14", 4400)])
        s = prices.summary("wheat", window=30)
        assert s["now"] == 44.0  # rupees per quintal / 100
        assert s["base"] == 40.0
        assert round(s["change_pct"], 6) == 10.0
        assert s["last_date"] == "2026-11-14"


def test_summary_averages_markets_and_filters_state(app):
    with app.app_context():
        upsert(get_db(), [
            rec("2026-11-14", 4000, "M1", "S1"), rec("2026-11-14", 5000, "M2", "S2"),
        ])
        assert prices.summary("wheat")["now"] == 45.0
        assert prices.summary("wheat", state="S1")["now"] == 40.0
        assert prices.summary("wheat", state="S1")["change_pct"] is None  # one day: no baseline


def test_summary_no_data(app):
    with app.app_context():
        s = prices.summary("nothing")
        assert s["now"] is None and s["points"] == []


def test_snapshot_skips_stale_commodities(app):
    with app.app_context():
        upsert(get_db(), [rec("2026-11-14", 4000), rec("2026-08-01", 3000, commodity="Old")])
        slugs = [c["slug"] for c in prices.commodity_snapshot()]
        assert slugs == ["wheat"]


def test_state_comparison_and_market_rows(demo):
    with demo.app_context():
        states = prices.state_comparison("wheat")
        assert len(states) == 6
        assert states == sorted(states, key=lambda s: s["price"])
        rows = prices.market_rows("wheat", limit=5)
        assert len(rows) == 5
        assert [r["modal"] for r in rows] == sorted(r["modal"] for r in rows)


def test_top_movers_shape(demo):
    with demo.app_context():
        movers = prices.top_movers(window=7)
        assert set(movers) == {"rises", "falls"}
        assert all(m["change_pct"] > 0 for m in movers["rises"])
        assert all(m["change_pct"] < 0 for m in movers["falls"])
