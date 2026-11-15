"""Price queries.

The source data is rupees per quintal (100 kg) per market. Everything returned from this
module is rupees per kg. A commodity's "price" on a day is the simple average of the modal
prices reported by the markets that day. Markets differ between days, so day-to-day moves in
this average include some noise from which markets reported. That is fine for a trend view.
"""

import re
from datetime import date, timedelta

from .db import get_db

QUINTAL_KG = 100.0
STALE_DAYS = 30


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "item"


def _per_kg(value):
    return None if value is None else value / QUINTAL_KG


def _minus_days(iso_date, days):
    return (date.fromisoformat(iso_date) - timedelta(days=days)).isoformat()


def data_latest_date():
    return get_db().execute("SELECT MAX(price_date) AS d FROM prices").fetchone()["d"]


def commodity_name(slug):
    row = get_db().execute("SELECT name FROM commodities WHERE slug = ?", (slug,)).fetchone()
    return row["name"] if row else None


def commodity_snapshot(limit=None):
    """Commodities with recent data, most widely reported first, with their latest price."""
    latest = data_latest_date()
    if not latest:
        return []
    sql = """
        SELECT p.commodity AS slug, c.name AS name, p.price_date AS latest,
               AVG(p.modal_price) AS avg_quintal, COUNT(DISTINCT p.market) AS markets
        FROM prices p
        JOIN commodities c ON c.slug = p.commodity
        JOIN (SELECT commodity, MAX(price_date) AS d FROM prices GROUP BY commodity) m
          ON m.commodity = p.commodity AND m.d = p.price_date
        WHERE p.price_date >= ?
        GROUP BY p.commodity
        ORDER BY markets DESC, c.name
    """
    args = [_minus_days(latest, STALE_DAYS)]
    if limit:
        sql += " LIMIT ?"
        args.append(limit)
    return [
        {
            "slug": r["slug"],
            "name": r["name"],
            "latest": r["latest"],
            "price": _per_kg(r["avg_quintal"]),
            "markets": r["markets"],
        }
        for r in get_db().execute(sql, args)
    ]


def all_states():
    rows = get_db().execute("SELECT DISTINCT state FROM prices ORDER BY state")
    return [r["state"] for r in rows]


def states_for(slug):
    rows = get_db().execute(
        "SELECT DISTINCT state FROM prices WHERE commodity = ? ORDER BY state", (slug,)
    )
    return [r["state"] for r in rows]


def latest_date(slug, state=None):
    sql = "SELECT MAX(price_date) AS d FROM prices WHERE commodity = ?"
    args = [slug]
    if state:
        sql += " AND state = ?"
        args.append(state)
    return get_db().execute(sql, args).fetchone()["d"]


def series(slug, days, state=None):
    """Daily average price for the `days` days up to the latest date with data."""
    end = latest_date(slug, state)
    if not end:
        return []
    sql = """
        SELECT price_date, AVG(modal_price) AS avg, MIN(min_price) AS lo, MAX(max_price) AS hi,
               COUNT(DISTINCT market) AS markets
        FROM prices WHERE commodity = ? AND price_date >= ?
    """
    args = [slug, _minus_days(end, days)]
    if state:
        sql += " AND state = ?"
        args.append(state)
    sql += " GROUP BY price_date ORDER BY price_date"
    return [
        {
            "date": r["price_date"],
            "avg": _per_kg(r["avg"]),
            "lo": _per_kg(r["lo"]),
            "hi": _per_kg(r["hi"]),
            "markets": r["markets"],
        }
        for r in get_db().execute(sql, args)
    ]


def summary(slug, state=None, window=30):
    """Latest price, change over `window` days, range, and the points to chart."""
    pts = series(slug, window + 7, state)  # extra lookback so there is a baseline to compare to
    empty = {
        "points": [], "now": None, "base": None, "change_pct": None,
        "lo": None, "hi": None, "last_date": None, "markets": 0,
    }
    if not pts:
        return empty
    last = pts[-1]
    cutoff = _minus_days(last["date"], window)
    window_pts = [p for p in pts if p["date"] >= cutoff]
    base_pt = None
    for p in pts:
        if p["date"] <= cutoff:
            base_pt = p
    if base_pt is not None:
        base = base_pt["avg"]
    elif len(window_pts) > 1:
        base = window_pts[0]["avg"]
    else:
        base = None
    values = [p["avg"] for p in window_pts]
    return {
        "points": window_pts,
        "now": last["avg"],
        "base": base,
        "change_pct": ((last["avg"] - base) / base * 100) if base else None,
        "lo": min(values),
        "hi": max(values),
        "last_date": last["date"],
        "markets": last["markets"],
    }


def chart_geometry(values, width=600, height=200, pad=0.2):
    """SVG polyline/polygon coordinates for a small line chart. Returns None for no data."""
    if not values:
        return None
    lo, hi = min(values), max(values)
    span = (hi - lo) or max(abs(hi) * 0.02, 1.0)
    lo, hi = lo - span * pad, hi + span * pad
    n = len(values)
    coords = []
    for i, v in enumerate(values):
        x = width * (i / (n - 1)) if n > 1 else width / 2
        y = height - (v - lo) / (hi - lo) * height
        coords.append((x, y))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"0,{height} {line} {width},{height}" if n > 1 else ""
    return {
        "line": line if n > 1 else "",
        "area": area,
        "last_x": f"{coords[-1][0]:.1f}",
        "last_y": f"{coords[-1][1]:.1f}",
    }


def market_rows(slug, state=None, limit=10):
    """Latest-day price in each market, cheapest first."""
    end = latest_date(slug, state)
    if not end:
        return []
    sql = """
        SELECT state, district, market, variety, min_price, max_price, modal_price
        FROM prices WHERE commodity = ? AND price_date = ?
    """
    args = [slug, end]
    if state:
        sql += " AND state = ?"
        args.append(state)
    sql += " ORDER BY modal_price, market LIMIT ?"
    args.append(limit)
    return [
        {
            "state": r["state"], "district": r["district"], "market": r["market"],
            "variety": r["variety"], "lo": _per_kg(r["min_price"]),
            "hi": _per_kg(r["max_price"]), "modal": _per_kg(r["modal_price"]),
        }
        for r in get_db().execute(sql, args)
    ]


def state_comparison(slug):
    """Average price by state over the last three days of data (states report on different days)."""
    end = latest_date(slug)
    if not end:
        return []
    rows = get_db().execute(
        """
        SELECT state, AVG(modal_price) AS avg, COUNT(DISTINCT market) AS markets
        FROM prices WHERE commodity = ? AND price_date >= ?
        GROUP BY state ORDER BY avg
        """,
        (slug, _minus_days(end, 2)),
    )
    return [
        {"state": r["state"], "price": _per_kg(r["avg"]), "markets": r["markets"]} for r in rows
    ]


def top_movers(window=7, limit=5, min_markets=3):
    """Biggest risers and fallers over `window` days among widely reported commodities."""
    moves = []
    for snap in commodity_snapshot(limit=60):
        if snap["markets"] < min_markets:
            continue
        s = summary(snap["slug"], None, window)
        if s["change_pct"] is None:
            continue
        moves.append({"slug": snap["slug"], "name": snap["name"], "now": s["now"],
                      "change_pct": s["change_pct"]})
    rises = sorted((m for m in moves if m["change_pct"] > 0), key=lambda m: -m["change_pct"])
    falls = sorted((m for m in moves if m["change_pct"] < 0), key=lambda m: m["change_pct"])
    return {"rises": rises[:limit], "falls": falls[:limit]}


def basket_summary(user_id, state=None):
    """The user's monthly basket priced at today's wholesale reference prices."""
    rows = get_db().execute(
        """
        SELECT b.id, b.commodity AS slug, b.qty_kg, COALESCE(c.name, b.commodity) AS name
        FROM basket_items b LEFT JOIN commodities c ON c.slug = b.commodity
        WHERE b.user_id = ? ORDER BY name
        """,
        (user_id,),
    ).fetchall()
    items, total, diff = [], 0.0, 0.0
    for r in rows:
        s = summary(r["slug"], state or None, 30)
        used_national = False
        if s["now"] is None and state:
            s = summary(r["slug"], None, 30)
            used_national = s["now"] is not None
        cost = r["qty_kg"] * s["now"] if s["now"] is not None else None
        change = r["qty_kg"] * (s["now"] - s["base"]) if None not in (s["now"], s["base"]) else None
        if cost is not None:
            total += cost
        if change is not None:
            diff += change
        items.append({
            "id": r["id"], "slug": r["slug"], "name": r["name"], "qty": r["qty_kg"],
            "price": s["now"], "cost": cost, "change": change,
            "change_pct": s["change_pct"], "national": used_national,
        })
    return {"items": items, "total": total, "diff": diff}


def purchase_summary(user_id, state=None, days=30):
    """What the user actually paid recently versus today's wholesale reference price."""
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = get_db().execute(
        """
        SELECT p.commodity AS slug, COALESCE(c.name, p.commodity) AS name,
               SUM(p.price_per_kg * p.qty_kg) / SUM(p.qty_kg) AS paid, SUM(p.qty_kg) AS qty
        FROM purchase_log p LEFT JOIN commodities c ON c.slug = p.commodity
        WHERE p.user_id = ? AND p.bought_on >= ?
        GROUP BY p.commodity ORDER BY name
        """,
        (user_id, since),
    ).fetchall()
    out = []
    for r in rows:
        s = summary(r["slug"], state or None, 7)
        if s["now"] is None and state:
            s = summary(r["slug"], None, 7)
        market = s["now"]
        out.append({
            "name": r["name"], "paid": r["paid"], "qty": r["qty"], "market": market,
            "diff_pct": ((r["paid"] - market) / market * 100) if market else None,
        })
    return out
