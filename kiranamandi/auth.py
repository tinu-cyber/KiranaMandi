"""Accounts, sessions and CSRF protection."""

import functools
import hmac
import re
import secrets
from datetime import datetime, timezone

from flask import (Blueprint, abort, flash, g, redirect, render_template, request, session,
                   url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db

bp = Blueprint("auth", __name__)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD = 8


def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def is_pro(user):
    return bool(user) and user["plan"] == "pro"


def clean_email(value):
    email = (value or "").strip().lower()
    return email if len(email) <= 254 and EMAIL_RE.match(email) else None


def _safe_next(target):
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for("main.dashboard")


@bp.before_app_request
def load_user_and_check_csrf():
    g.user = None
    uid = session.get("user_id")
    if uid is not None:
        g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
        if g.user is None:
            session.clear()
    if request.method == "POST":
        expected = session.get("_csrf")
        sent = request.form.get("_csrf", "")
        if not expected or not hmac.compare_digest(sent.encode(), expected.encode()):
            abort(400)


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def pro_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.path))
        if not is_pro(g.user):
            flash("That feature is part of the Pro plan.", "error")
            return redirect(url_for("main.dashboard"))
        return view(*args, **kwargs)

    return wrapped


def _start_session(user_id):
    session.clear()  # new session on login prevents fixation
    session["user_id"] = user_id
    session.permanent = True


@bp.route("/signup", methods=("GET", "POST"))
def signup():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = clean_email(request.form.get("email"))
        password = request.form.get("password", "")
        error = None
        if not email:
            error = "Enter a valid email address."
        elif len(password) < MIN_PASSWORD:
            error = f"Use a password of at least {MIN_PASSWORD} characters."
        elif len(password) > 200:
            error = "That password is too long."
        if error is None:
            db = get_db()
            if db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
                error = "An account with that email already exists. Try logging in."
            else:
                cur = db.execute(
                    "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                    (email, generate_password_hash(password),
                     datetime.now(timezone.utc).isoformat(timespec="seconds")),
                )
                db.commit()
                _start_session(cur.lastrowid)
                flash("Welcome! Your free account is ready.", "ok")
                return redirect(url_for("main.dashboard"))
        flash(error, "error")
    return render_template("signup.html")


@bp.route("/login", methods=("GET", "POST"))
def login():
    if g.user:
        return redirect(url_for("main.dashboard"))
    if request.method == "POST":
        email = clean_email(request.form.get("email")) or ""
        password = request.form.get("password", "")
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("That email and password don't match.", "error")
        else:
            _start_session(user["id"])
            return redirect(_safe_next(request.args.get("next")))
    return render_template("login.html")


@bp.post("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "ok")
    return redirect(url_for("main.index"))
