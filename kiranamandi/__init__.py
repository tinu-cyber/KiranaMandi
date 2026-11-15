"""KiranaMandi: daily wholesale prices for raw ingredients, with alerts and a basket cost tracker."""

import logging
import os
from datetime import timedelta
from pathlib import Path

from flask import Flask, g, render_template

from . import auth, cli, db, formatting, views

INSECURE_KEYS = {"", "dev-change-me", "change-me-to-a-long-random-string"}
ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """Minimal .env reader so the app and cron jobs share one config file (no extra package)."""
    if not path.is_file():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def create_app(test_config=None):
    _load_dotenv(ROOT / ".env")
    app = Flask(__name__, instance_relative_config=True)
    env = os.environ.get
    app.config.from_mapping(
        SECRET_KEY=env("SECRET_KEY", "dev-change-me"),
        DATABASE=env("DATABASE_PATH", str(Path(app.instance_path) / "kiranamandi.sqlite")),
        APP_NAME=env("APP_NAME", "KiranaMandi"),
        BASE_URL=env("BASE_URL", "http://localhost:5000"),
        PRO_PRICE_INR=int(env("PRO_PRICE_INR", "399")),
        DATA_GOV_API_KEY=env("DATA_GOV_API_KEY", ""),
        DATA_GOV_RESOURCE_ID=env("DATA_GOV_RESOURCE_ID", "9ef84268-d588-465a-a308-a864a43d0070"),
        SMTP_HOST=env("SMTP_HOST", ""),
        SMTP_PORT=int(env("SMTP_PORT", "587")),
        SMTP_USER=env("SMTP_USER", ""),
        SMTP_PASSWORD=env("SMTP_PASSWORD", ""),
        MAIL_FROM=env("MAIL_FROM", "KiranaMandi <alerts@localhost>"),
        TELEGRAM_BOT_TOKEN=env("TELEGRAM_BOT_TOKEN", ""),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=env("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
    )
    if test_config:
        app.config.update(test_config)
    if app.config["SECRET_KEY"] in INSECURE_KEYS and not (app.debug or app.testing):
        raise RuntimeError("Set SECRET_KEY to a long random string (see .env.example).")

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO)
    db.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    cli.register(app)

    app.jinja_env.globals["csrf_token"] = auth.csrf_token
    app.jinja_env.filters.update(
        inr=formatting.inr, inr1=formatting.inr1, pct=formatting.pct,
        chg=formatting.change_class,
    )

    @app.context_processor
    def inject():
        user = g.get("user")
        return {
            "app_name": app.config["APP_NAME"],
            "current_user": user,
            "is_pro": auth.is_pro(user),
            "pro_price": app.config["PRO_PRICE_INR"],
            "demo_data": db.get_meta("demo_data") == "1",
        }

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; img-src 'self' data:; script-src 'self'; "
            "form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
        )
        if app.config["SESSION_COOKIE_SECURE"]:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000"
        return resp

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("message.html", heading="Page not found",
                               body="We couldn't find that page."), 404

    @app.errorhandler(400)
    def bad_request(_e):
        return render_template("message.html", heading="That didn't work",
                               body="Your session may have expired. Go back, refresh the page "
                                    "and try again."), 400

    return app
