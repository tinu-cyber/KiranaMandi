"""Public pages and the signed-in dashboard."""

import re
from datetime import date, datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect, render_template,
                   request, url_for)

from . import notify, prices
from .auth import clean_email, is_pro, login_required, pro_required
from .db import get_db

bp = Blueprint("main", __name__)

FREE_MAX_DAYS = 30
RANGES = (7, 30, 90, 365)
EXAMPLE_QTY = (40, 120, 30, 25)  # kg per month in the landing page's example basket
MAX_BASKET_ITEMS = 40
MAX_ALERTS = 20
TELEGRAM_ID_RE = re.compile(r"^-?\d{1,20}$")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _number(key, high):
    """Positive float from the form, or None if missing, malformed or out of range."""
    try:
        value = float(request.form.get(key, "").replace(",", "").strip())
    except ValueError:
        return None
    return value if 0 < value <= high else None


def _dashboard():
    return redirect(url_for("main.dashboard"))


@bp.route("/")
def index():
    panels = []
    for snap in prices.commodity_snapshot(limit=4):
        s = prices.summary(snap["slug"], None, 30)
        panels.append({**snap, **s, "chart": prices.chart_geometry([p["avg"] for p in s["points"]])})

    basket, total, diff = [], 0.0, 0.0
    for panel, qty in zip(panels, EXAMPLE_QTY):
        if panel["now"] is None:
            continue
        change = qty * (panel["now"] - panel["base"]) if panel["base"] else None
        basket.append({"name": panel["name"], "qty": qty, "price": panel["now"],
                       "cost": qty * panel["now"], "change": change})
        total += qty * panel["now"]
        diff += change or 0.0

    example_alert = None
    if panels and panels[0]["now"]:
        example_alert = {"name": panels[0]["name"], "price": panels[0]["now"],
                         "limit": round(panels[0]["now"] * 0.98)}
    return render_template("index.html", panels=panels, basket=basket, basket_total=total,
                           basket_diff=diff, example_alert=example_alert)


@bp.post("/subscribe")
def subscribe():
    email = clean_email(request.form.get("email"))
    if not email:
        flash("Enter a valid email address.", "error")
        return redirect(url_for("main.index") + "#signup")
    db = get_db()
    db.execute("INSERT OR IGNORE INTO subscribers (email, created_at) VALUES (?, ?)",
               (email, _now()))
    db.commit()
    flash("You're on the list. The first summary arrives with the next weekly send.", "ok")
    return redirect(url_for("main.index"))


@bp.route("/unsubscribe/<token>")
def unsubscribe(token):
    email = notify.read_unsubscribe_token(token)
    if not email:
        abort(404)
    db = get_db()
    db.execute("DELETE FROM subscribers WHERE email = ?", (email,))
    db.execute("UPDATE users SET digest_opt_in = 0 WHERE email = ?", (email,))
    db.commit()
    return render_template("message.html", heading="You're unsubscribed",
                           body="You won't receive the weekly summary any more.")


@bp.route("/prices")
def prices_index():
    return render_template("prices.html", items=prices.commodity_snapshot())


@bp.route("/prices/<slug>")
def commodity(slug):
    name = prices.commodity_name(slug)
    if name is None:
        abort(404)
    pro = is_pro(g.user)
    days = request.args.get("days", 30, type=int)
    if days not in RANGES:
        days = 30
    limited = False
    if days > FREE_MAX_DAYS and not pro:
        days, limited = 30, True
    states = prices.states_for(slug)
    state = request.args.get("state", "")
    if state not in states:
        state = ""
    s = prices.summary(slug, state or None, days)
    return render_template(
        "commodity.html", slug=slug, name=name, days=days, state=state, states=states, s=s,
        limited=limited, chart_data=prices.chart_geometry([p["avg"] for p in s["points"]]),
        ranges=[{"days": d, "locked": d > FREE_MAX_DAYS and not pro} for d in RANGES],
        markets=prices.market_rows(slug, state or None, limit=50 if pro else 10),
        comparison=prices.state_comparison(slug) if pro else None,
    )


@bp.route("/healthz")
def healthz():
    return jsonify(status="ok", latest_price_date=prices.data_latest_date())


@bp.route("/app")
@login_required
def dashboard():
    db, user = get_db(), g.user
    pro = is_pro(user)
    return render_template(
        "dashboard.html",
        commodities=prices.commodity_snapshot(),
        states=prices.all_states(),
        basket=prices.basket_summary(user["id"], user["state"]) if pro else None,
        purchases=prices.purchase_summary(user["id"], user["state"]) if pro else [],
        alerts=db.execute(
            "SELECT a.*, COALESCE(c.name, a.commodity) AS name FROM alerts a "
            "LEFT JOIN commodities c ON c.slug = a.commodity WHERE a.user_id = ? ORDER BY name",
            (user["id"],),
        ).fetchall() if pro else [],
        requested=db.execute("SELECT 1 FROM pro_requests WHERE user_id = ?",
                             (user["id"],)).fetchone() is not None,
        today=date.today().isoformat(),
        telegram_enabled=bool(current_app.config["TELEGRAM_BOT_TOKEN"]),
    )


@bp.post("/app/settings")
@login_required
def settings():
    state = request.form.get("state", "")
    chat_id = request.form.get("telegram_chat_id", "").strip()
    if state and state not in prices.all_states():
        state = ""
    if chat_id and not TELEGRAM_ID_RE.match(chat_id):
        flash("A Telegram chat ID is a number such as 123456789.", "error")
        return _dashboard()
    db = get_db()
    db.execute(
        "UPDATE users SET state = ?, telegram_chat_id = ?, digest_opt_in = ? WHERE id = ?",
        (state, chat_id, 1 if request.form.get("digest_opt_in") else 0, g.user["id"]),
    )
    db.commit()
    flash("Settings saved.", "ok")
    return _dashboard()


@bp.post("/app/upgrade")
@login_required
def upgrade():
    db = get_db()
    db.execute("INSERT OR IGNORE INTO pro_requests (user_id, requested_at) VALUES (?, ?)",
               (g.user["id"], _now()))
    db.commit()
    flash("Thanks. We'll email you when Pro access is switched on for your account.", "ok")
    return _dashboard()


@bp.post("/app/basket")
@pro_required
def basket_add():
    slug, qty = request.form.get("commodity", ""), _number("qty_kg", 1_000_000)
    db = get_db()
    count = db.execute("SELECT COUNT(*) AS n FROM basket_items WHERE user_id = ?",
                       (g.user["id"],)).fetchone()["n"]
    if prices.commodity_name(slug) is None or qty is None:
        flash("Choose a commodity and enter a monthly quantity in kg.", "error")
    elif count >= MAX_BASKET_ITEMS and not db.execute(
            "SELECT 1 FROM basket_items WHERE user_id = ? AND commodity = ?",
            (g.user["id"], slug)).fetchone():
        flash(f"A basket can hold up to {MAX_BASKET_ITEMS} items.", "error")
    else:
        db.execute(
            "INSERT INTO basket_items (user_id, commodity, qty_kg) VALUES (?, ?, ?) "
            "ON CONFLICT(user_id, commodity) DO UPDATE SET qty_kg = excluded.qty_kg",
            (g.user["id"], slug, qty))
        db.commit()
    return _dashboard()


@bp.post("/app/basket/<int:item_id>/delete")
@pro_required
def basket_delete(item_id):
    db = get_db()
    db.execute("DELETE FROM basket_items WHERE id = ? AND user_id = ?", (item_id, g.user["id"]))
    db.commit()
    return _dashboard()


@bp.post("/app/alerts")
@pro_required
def alert_add():
    slug, state = request.form.get("commodity", ""), request.form.get("state", "")
    direction, limit = request.form.get("direction", ""), _number("threshold", 1_000_000)
    db = get_db()
    count = db.execute("SELECT COUNT(*) AS n FROM alerts WHERE user_id = ?",
                       (g.user["id"],)).fetchone()["n"]
    if state and state not in prices.all_states():
        state = ""
    if prices.commodity_name(slug) is None or direction not in ("above", "below") or limit is None:
        flash("Choose a commodity, a direction and a price limit in rupees per kg.", "error")
    elif count >= MAX_ALERTS:
        flash(f"You can have up to {MAX_ALERTS} alerts.", "error")
    else:
        db.execute(
            "INSERT INTO alerts (user_id, commodity, state, direction, threshold_per_kg) "
            "VALUES (?, ?, ?, ?, ?)", (g.user["id"], slug, state, direction, limit))
        db.commit()
        flash("Alert added.", "ok")
    return _dashboard()


@bp.post("/app/alerts/<int:alert_id>/delete")
@pro_required
def alert_delete(alert_id):
    db = get_db()
    db.execute("DELETE FROM alerts WHERE id = ? AND user_id = ?", (alert_id, g.user["id"]))
    db.commit()
    return _dashboard()


@bp.post("/app/purchases")
@pro_required
def purchase_add():
    slug = request.form.get("commodity", "")
    price, qty = _number("price_per_kg", 100_000), _number("qty_kg", 1_000_000)
    try:
        bought = date.fromisoformat(request.form.get("bought_on", ""))
    except ValueError:
        bought = None
    if bought and not (date.today() - timedelta(days=400) <= bought <= date.today()):
        bought = None
    if prices.commodity_name(slug) is None or None in (price, qty, bought):
        flash("Enter a commodity, a recent date, the price you paid per kg and the quantity.", "error")
    else:
        db = get_db()
        db.execute(
            "INSERT INTO purchase_log (user_id, commodity, bought_on, price_per_kg, qty_kg) "
            "VALUES (?, ?, ?, ?, ?)", (g.user["id"], slug, bought.isoformat(), price, qty))
        db.commit()
        flash("Purchase logged.", "ok")
    return _dashboard()
