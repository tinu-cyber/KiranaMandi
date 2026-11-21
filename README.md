# KiranaMandi

Daily wholesale prices for raw ingredients (wheat, rice, dal, onion and more), with price alerts and a monthly basket cost tracker. Built for cloud kitchens, caterers, small restaurants and traders who buy staples every week.

The name shown on the site comes from `APP_NAME` in `.env`.

## What it does

| | Free | Pro |
|---|---|---|
| Price page for every commodity, 7 and 30 day charts, filter by state | yes | yes |
| Market-by-market prices | 10 cheapest | up to 50 |
| Weekly summary email | yes | yes, with basket total |
| 90 day and 1 year history | | yes |
| All-states comparison | | yes |
| Price alerts (email and Telegram) | | yes |
| Monthly basket cost tracker | | yes |
| Log what you paid and compare with wholesale | | yes |

Billing is not built yet. Users request Pro from their dashboard and you switch them on by hand (see "Plans and billing").

## Run it locally

Needs Python 3.10 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # for local use you can leave the defaults and set FLASK_DEBUG=1
export FLASK_DEBUG=1
flask --app wsgi seed-demo      # 60 days of clearly labelled synthetic prices
flask --app wsgi run
```

Open http://localhost:5000. Demo data shows a banner on every page. The first real `ingest` or `import-csv` deletes the demo data automatically (and refuses to touch real data).

Run the tests with `python -m pytest`.

## Getting real data in

The app stores one row per market, commodity, variety and day, in rupees per quintal, and shows rupees per kg.

1. **Daily from data.gov.in.** Register a free account at https://data.gov.in (the developer pages are at https://data.gov.in/developers), log in, open "My Account" to find your API key, and put it in `DATA_GOV_API_KEY`. The site's layout changes now and then, so look for the API key section if the menu names differ. Then run `flask --app wsgi ingest` once a day (cron example in `deploy/`). The default `DATA_GOV_RESOURCE_ID` is the Agmarknet daily mandi price dataset.
2. **History from CSV.** `flask --app wsgi import-csv path/to/prices.csv`. Column names are matched loosely, so Agmarknet downloads such as "Market Name", "Modal Price (Rs./Quintal)" and "Reported Date" load as they are. Use this to backfill, because the daily API only gives the current day and history otherwise builds up one day at a time.

Things to check before relying on it:

- **The ingest command has not been run against the live API.** It was built from the published record format and tested with mocked responses. Run it once with your key and look at the output. data.gov.in has listed this dataset with and without a public API, so confirm the resource id is current.
- **Coverage.** Agmarknet covers raw agricultural commodities (grains, pulses, vegetables, oilseeds). It does not have packaged items such as refined oil or sugar. The Department of Consumer Affairs publishes retail and wholesale prices for those at fcainfoweb.nic.in, but only as a website, so load them through `import-csv` if their terms suit you.
- **Licence.** Check the reuse terms of each dataset before running this as a paid product, and keep the attribution in the footer.
- **How the price is computed.** A commodity's price for a day is the average of the modal prices reported by markets that day. Which markets report changes from day to day, so the line has some noise.

## Alerts and the weekly summary

- `flask --app wsgi check-alerts` sends alerts that have just crossed their limit. An alert fires once, then re-arms when the price moves back. Run it after each ingest.
- `flask --app wsgi send-digest` emails the weekly summary (`--dry-run` prints it instead). It goes to account holders who have not opted out and to people who signed up on the home page. Every email carries a signed unsubscribe link.
- Set `SMTP_*` for email. With no `SMTP_HOST`, messages are written to the log instead of sent, which is handy in development.
- Set `TELEGRAM_BOT_TOKEN` (create a bot with @BotFather) to enable Telegram. Users paste their numeric chat ID in their dashboard settings.

## Plans and billing

`flask --app wsgi pro-requests` lists who asked for Pro. `flask --app wsgi set-plan someone@example.com pro` switches a user on, and `... free` switches them off. Their alerts stop sending when they drop to free.

Taking payments is the next piece to build. Razorpay Subscriptions suits Indian customers: add a checkout route, a webhook that calls the same plan update, and replace the "Request Pro access" button.

## Deploying on Lightsail

These steps assume the server already runs Caddy (as it does for MarketAnalyzer). KiranaMandi runs under gunicorn on `127.0.0.1:8001` and Caddy sends traffic to it. Port 8000 is left for MarketAnalyzer. If you use nginx instead, `deploy/nginx.conf` still works.

Before you start, in the Lightsail console: make sure the instance has a **static IP**. With a domain, open ports **80 and 443** under Networking > IPv4 firewall and point the domain's A record at the static IP. Without one, open **8444** instead (see option B in `deploy/Caddyfile.example`).

On Ubuntu:

```bash
sudo apt update && sudo apt install -y python3-venv sqlite3 git
sudo useradd --system --home-dir /opt/kiranamandi --shell /usr/sbin/nologin kiranamandi
sudo mkdir /opt/kiranamandi && sudo chown kiranamandi: /opt/kiranamandi
sudo -u kiranamandi git clone https://github.com/tinu-cyber/KiranaMandi.git /opt/kiranamandi
cd /opt/kiranamandi && sudo -u kiranamandi python3 -m venv .venv && sudo -u kiranamandi .venv/bin/pip install -r requirements.txt
sudo mkdir -p /var/lib/kiranamandi /var/backups/kiranamandi /var/log/kiranamandi && sudo chown kiranamandi: /var/lib/kiranamandi /var/backups/kiranamandi /var/log/kiranamandi
python3 -c "import secrets; print(secrets.token_urlsafe(48))"     # copy this for SECRET_KEY
sudo -u kiranamandi cp .env.example .env && sudo -u kiranamandi nano .env   # set SECRET_KEY, BASE_URL, DATABASE_PATH=/var/lib/kiranamandi/kiranamandi.sqlite, keys
sudo cp deploy/kiranamandi.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable --now kiranamandi
curl -s http://127.0.0.1:8001/healthz                              # should answer before you touch Caddy
sudo nano /etc/caddy/Caddyfile                                     # paste one block from deploy/Caddyfile.example
sudo caddy validate --config /etc/caddy/Caddyfile && sudo systemctl reload caddy
sudo -u kiranamandi .venv/bin/flask --app wsgi ingest      # load the first prices (needs DATA_GOV_API_KEY)
sudo -u kiranamandi crontab deploy/crontab.example
```

Check it with `curl https://your-domain.example/healthz`, which shows the latest price date. Keep `BASE_URL` set to the public https address (it goes into email links), `COOKIE_SECURE=1` and `TRUST_PROXY=1`. To try the site before you have an API key, run `sudo -u kiranamandi .venv/bin/flask --app wsgi seed-demo` instead of `ingest` (the site then shows a demo-data banner until real data replaces it).

Two gunicorn workers use roughly 100-150 MB of memory. On a 2 GB server shared with other apps, add a swapfile.

To deploy an update: `cd /opt/kiranamandi && sudo -u kiranamandi git pull && sudo systemctl restart kiranamandi`. If the logs misbehave, `sudo journalctl -u kiranamandi -e` shows them.

The app refuses to start outside debug mode if `SECRET_KEY` is missing or still a placeholder. Turn on Lightsail snapshots as well as the nightly SQLite backup.

SQLite (in WAL mode) is plenty for the first few thousand users. If you outgrow it, the queries are plain SQL and `db.py` is the only place that opens a connection.

## Security notes

- Passwords are hashed with Werkzeug, sessions are signed cookies (set `COOKIE_SECURE=1` behind HTTPS), and every form carries a CSRF token.
- A Content Security Policy blocks inline scripts and styles, so keep new styling in `static/style.css`.
- `/login` and `/signup` are rate-limited in the app (10 attempts per IP per 10 minutes, shared across workers). There is no email verification or password reset yet.
- API keys and the Telegram token are never written to logs.

## Layout

```
kiranamandi/             the Flask app (views, auth, prices, ingest, notify, cli, templates, static)
tests/            pytest suite
deploy/           systemd, Caddy, nginx and cron examples
wsgi.py           entry point: gunicorn wsgi:app, flask --app wsgi <command>
```

## Ideas for next

Razorpay billing, email verification and password reset, per-user supplier price lists, WhatsApp alerts (needs the WhatsApp Business API), a retail-price source for packaged goods.
