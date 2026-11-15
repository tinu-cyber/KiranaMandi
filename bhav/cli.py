"""Command line tasks, run as `flask --app wsgi <command>` (used by cron on the server)."""

from datetime import datetime

import click
from flask import current_app

from . import ingest, notify
from .db import get_db, init_db


def register(app):
    @app.cli.command("init-db")
    def init_db_command():
        """Create the database tables (also done automatically on start)."""
        init_db()
        click.echo("Database ready.")

    @app.cli.command("seed-demo")
    @click.option("--days", default=60, show_default=True)
    def seed_demo_command(days):
        """Load clearly labelled synthetic prices so you can try the app without an API key."""
        try:
            n = ingest.seed_demo(get_db(), days=days)
        except ingest.IngestError as exc:
            raise click.ClickException(str(exc))
        click.echo(f"Loaded {n} demo price rows.")

    @app.cli.command("ingest")
    def ingest_command():
        """Fetch the latest daily mandi prices from data.gov.in (run daily from cron)."""
        cfg = current_app.config
        try:
            n = ingest.ingest_latest(get_db(), cfg["DATA_GOV_API_KEY"], cfg["DATA_GOV_RESOURCE_ID"])
        except ingest.IngestError as exc:
            raise click.ClickException(str(exc))
        click.echo(f"{datetime.now():%Y-%m-%d %H:%M} ingested {n} rows.")

    @app.cli.command("import-csv")
    @click.argument("path", type=click.Path(exists=True, dir_okay=False))
    def import_csv_command(path):
        """Load historical prices from a CSV file (rupees per quintal)."""
        try:
            n = ingest.import_csv(get_db(), path)
        except ingest.IngestError as exc:
            raise click.ClickException(str(exc))
        click.echo(f"Imported {n} rows.")

    @app.cli.command("check-alerts")
    def check_alerts_command():
        """Send any price alerts that have just triggered (run after each ingest)."""
        click.echo(f"{notify.check_alerts()} alert(s) fired.")

    @app.cli.command("send-digest")
    @click.option("--dry-run", is_flag=True, help="Show the message without sending it.")
    def send_digest_command(dry_run):
        """Send the weekly price summary (run weekly from cron)."""
        count, sample = notify.send_digest(dry_run=dry_run)
        if sample and dry_run:
            click.echo(sample + "\n")
        click.echo(f"Digest {'would go' if dry_run else 'sent'} to {count} recipient(s).")

    @app.cli.command("set-plan")
    @click.argument("email")
    @click.argument("plan", type=click.Choice(["free", "pro"]))
    def set_plan_command(email, plan):
        """Switch a user between the free and pro plans."""
        db = get_db()
        cur = db.execute("UPDATE users SET plan = ? WHERE email = ?", (plan, email.strip().lower()))
        db.commit()
        if not cur.rowcount:
            raise click.ClickException("No user with that email.")
        click.echo(f"{email} is now on the {plan} plan.")

    @app.cli.command("pro-requests")
    def pro_requests_command():
        """List users who asked for Pro access."""
        rows = get_db().execute(
            "SELECT u.email, u.plan, r.requested_at FROM pro_requests r "
            "JOIN users u ON u.id = r.user_id ORDER BY r.requested_at"
        ).fetchall()
        for r in rows:
            click.echo(f"{r['requested_at']}  {r['email']}  ({r['plan']})")
        if not rows:
            click.echo("No requests.")
