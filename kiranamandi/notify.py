"""Alerts and the weekly digest, delivered by email and Telegram."""

import logging
import smtplib
from email.message import EmailMessage

import requests
from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

from . import prices
from .db import get_db
from .formatting import inr, inr1, pct

log = logging.getLogger(__name__)


def _serializer():
    return URLSafeSerializer(current_app.config["SECRET_KEY"], salt="unsubscribe")


def unsubscribe_url(email):
    token = _serializer().dumps(email)
    return f"{current_app.config['BASE_URL'].rstrip('/')}/unsubscribe/{token}"


def read_unsubscribe_token(token):
    try:
        return _serializer().loads(token)
    except BadSignature:
        return None


def send_email(to, subject, body):
    cfg = current_app.config
    if not cfg["SMTP_HOST"]:
        log.info("SMTP not configured, email not sent. To: %s | %s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["MAIL_FROM"], to, subject
    msg.set_content(body)
    try:
        if cfg["SMTP_PORT"] == 465:
            server = smtplib.SMTP_SSL(cfg["SMTP_HOST"], 465, timeout=30)
        else:
            server = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=30)
            server.starttls()
        with server:
            if cfg["SMTP_USER"]:
                server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            server.send_message(msg)
        return True
    except (OSError, smtplib.SMTPException) as exc:
        log.error("Email to %s failed: %s", to, type(exc).__name__)
        return False


def send_telegram(chat_id, text):
    token = current_app.config["TELEGRAM_BOT_TOKEN"]
    if not (token and chat_id):
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text}, timeout=15,
        )
        if resp.status_code != 200:
            log.error("Telegram send failed: HTTP %s", resp.status_code)
            return False
        return True
    except requests.RequestException as exc:
        log.error("Telegram send failed: %s", type(exc).__name__)  # the URL contains the token
        return False


def check_alerts():
    """Fire alerts whose condition has just become true. An alert fires once when the price
    crosses the limit and re-arms when the price goes back to the other side."""
    db = get_db()
    rows = db.execute(
        """
        SELECT a.id, a.commodity, a.state, a.direction, a.threshold_per_kg, a.triggered,
               u.email, u.telegram_chat_id
        FROM alerts a JOIN users u ON u.id = a.user_id
        WHERE a.active = 1 AND u.plan = 'pro'
        """
    ).fetchall()
    fired = 0
    for a in rows:
        pts = prices.series(a["commodity"], 3, a["state"] or None)
        if not pts:
            continue
        price, on = pts[-1]["avg"], pts[-1]["date"]
        limit = a["threshold_per_kg"]
        hit = price > limit if a["direction"] == "above" else price < limit
        if hit and not a["triggered"]:
            name = prices.commodity_name(a["commodity"]) or a["commodity"]
            where = f" in {a['state']}" if a["state"] else ""
            word = "above" if a["direction"] == "above" else "below"
            text = (f"{name} is {word} your {inr1(limit)}/kg limit{where}. "
                    f"Wholesale reference price on {on}: {inr1(price)}/kg.")
            send_email(a["email"], f"Price alert: {name}", text)
            send_telegram(a["telegram_chat_id"], text)
            db.execute("UPDATE alerts SET triggered = 1, last_fired_on = ? WHERE id = ?",
                       (on, a["id"]))
            fired += 1
        elif not hit and a["triggered"]:
            db.execute("UPDATE alerts SET triggered = 0 WHERE id = ?", (a["id"],))
    db.commit()
    return fired


def build_digest(movers, as_of, unsubscribe, basket=None):
    lines = [f"{current_app.config['APP_NAME']} weekly price summary",
             f"Wholesale reference prices up to {as_of}.", ""]
    for title, items in (("Biggest rises this week", movers["rises"]),
                         ("Biggest falls this week", movers["falls"])):
        if items:
            lines.append(title + ":")
            lines += [f"  {m['name']}: {inr1(m['now'])}/kg ({pct(m['change_pct'])})" for m in items]
            lines.append("")
    if basket and basket["items"]:
        lines.append(f"Your basket: {inr(basket['total'])} per month at today's prices "
                     f"({inr(basket['diff'])} vs 30 days ago).")
        lines.append("")
    lines += [
        "Prices are average wholesale (mandi) prices from Government of India open data.",
        "Your own supplier's price may differ.",
        "",
        f"Unsubscribe: {unsubscribe}",
    ]
    return "\n".join(lines)


def send_digest(dry_run=False):
    """Email the weekly summary. Returns (recipients, sample_body)."""
    db = get_db()
    as_of = prices.data_latest_date()
    movers = prices.top_movers(window=7)
    if not as_of or not (movers["rises"] or movers["falls"]):
        return 0, None
    users = db.execute(
        "SELECT id, email, plan, state FROM users WHERE digest_opt_in = 1"
    ).fetchall()
    emails = {u["email"] for u in users}
    extra = [r["email"] for r in db.execute("SELECT email FROM subscribers")
             if r["email"] not in emails]
    sample, sent = None, 0
    for u in users:
        basket = prices.basket_summary(u["id"], u["state"]) if u["plan"] == "pro" else None
        body = build_digest(movers, as_of, unsubscribe_url(u["email"]), basket)
        sample = sample or body
        if not dry_run:
            send_email(u["email"], "Your weekly price summary", body)
        sent += 1
    for email in extra:
        body = build_digest(movers, as_of, unsubscribe_url(email))
        sample = sample or body
        if not dry_run:
            send_email(email, "Your weekly price summary", body)
        sent += 1
    return sent, sample
