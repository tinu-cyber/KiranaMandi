"""Getting price data in: the data.gov.in (Agmarknet) API, CSV files, and demo data."""

import csv
import logging
import random
import time
from datetime import date, datetime, timedelta

import requests

from .db import get_meta, set_meta
from .prices import slugify

log = logging.getLogger(__name__)

API_URL = "https://api.data.gov.in/resource/{resource_id}"
DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d-%b-%Y", "%d %b %Y", "%d-%b-%y")


class IngestError(Exception):
    pass


def parse_date(text):
    text = (text or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _num(value):
    try:
        number = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return number if number == number and number > 0 else None  # drops NaN, zero, negatives


def normalise(rec):
    """Turn one source record into a row tuple, or None if it is unusable."""
    day = parse_date(rec.get("date") or rec.get("arrival_date"))
    state = (rec.get("state") or "").strip()
    market = (rec.get("market") or "").strip()
    name = (rec.get("commodity") or "").strip()
    modal = _num(rec.get("modal_price"))
    if not (day and state and market and name and modal):
        return None
    return (
        day, state, (rec.get("district") or "").strip(), market, slugify(name),
        (rec.get("variety") or "").strip(), _num(rec.get("min_price")),
        _num(rec.get("max_price")), modal, name,
    )


def upsert(db, records):
    """Insert or update price rows. Returns how many usable rows were written."""
    rows = [t for t in (normalise(r) for r in records) if t]
    names = {(t[4], t[9]) for t in rows}
    db.executemany(
        "INSERT INTO commodities (slug, name) VALUES (?, ?) ON CONFLICT(slug) DO NOTHING",
        sorted(names),
    )
    db.executemany(
        """
        INSERT INTO prices (price_date, state, district, market, commodity, variety,
                            min_price, max_price, modal_price)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (price_date, state, market, commodity, variety) DO UPDATE SET
            district = excluded.district, min_price = excluded.min_price,
            max_price = excluded.max_price, modal_price = excluded.modal_price
        """,
        [t[:9] for t in rows],
    )
    db.commit()
    return len(rows)


def clear_demo_data(db):
    """Real data replaces demo data. Only ever deletes when the demo flag is set."""
    if get_meta("demo_data") == "1":
        db.execute("DELETE FROM prices")
        db.execute("DELETE FROM commodities")
        db.commit()
        set_meta("demo_data", "0")
        log.info("Removed demo data")


def _get_json(session, url, params):
    for attempt in range(3):
        try:
            resp = session.get(url, params=params, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            error = f"HTTP {resp.status_code}"
        except (requests.RequestException, ValueError) as exc:
            error = type(exc).__name__  # never include the URL: it carries the API key
        if attempt < 2:
            time.sleep(2 * (attempt + 1))
    raise IngestError(f"data.gov.in request failed ({error})")


def fetch_agmarknet(api_key, resource_id, page_size=1000, max_pages=100, proxy=""):
    """Download the current daily mandi prices from the data.gov.in API.

    data.gov.in refuses connections from many non-Indian IPs. On a server outside India,
    set DATA_GOV_PROXY to an HTTP proxy running in India (see deploy/india-proxy.md).
    """
    if not api_key:
        raise IngestError("DATA_GOV_API_KEY is not set")
    session = requests.Session()
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
    url = API_URL.format(resource_id=resource_id)
    records, offset = [], 0
    for _ in range(max_pages):
        data = _get_json(session, url, {
            "api-key": api_key, "format": "json", "limit": page_size, "offset": offset,
        })
        page = data.get("records") or []
        records.extend(page)
        offset += len(page)
        total = int(data.get("total") or 0)
        if len(page) < page_size or (total and offset >= total):
            break
    return records


def ingest_latest(db, api_key, resource_id, proxy=""):
    records = fetch_agmarknet(api_key, resource_id, proxy=proxy)
    if not records:
        raise IngestError("The API returned no records")
    clear_demo_data(db)
    written = upsert(db, records)
    set_meta("last_ingest", datetime.now().isoformat(timespec="seconds"))
    return written


# Header aliases so CSV downloads from Agmarknet or research mirrors load without editing.
def _canon(header):
    h = " ".join(header.lower().replace("_", " ").split())
    if "min" in h and "price" in h:
        return "min_price"
    if "max" in h and "price" in h:
        return "max_price"
    if "modal" in h and "price" in h:
        return "modal_price"
    if "state" in h:
        return "state"
    if "district" in h:
        return "district"
    if "market" in h:
        return "market"
    if "variety" in h:
        return "variety"
    if "date" in h:
        return "date"
    if "commodity" in h:
        return "commodity"
    return h


def import_csv(db, path):
    """Load a CSV with state, district, market, commodity, variety, date and min/max/modal
    price columns (rupees per quintal). Column names are matched loosely."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        records = [{_canon(k): v for k, v in row.items() if k} for row in reader]
    if not records:
        raise IngestError("The CSV has no rows")
    clear_demo_data(db)
    return upsert(db, records)


# name, base price (rupees/quintal), drift per day, number of states reporting
DEMO_COMMODITIES = [
    ("Wheat", 2650, 0.0004, 6), ("Rice", 3900, 0.0003, 6),
    ("Arhar (Tur/Red Gram)(Whole)", 7600, 0.0012, 6), ("Onion", 2300, -0.0006, 6),
    ("Bengal Gram(Gram)(Whole)", 5900, 0.0002, 5), ("Potato", 1900, 0.0, 5),
    ("Tomato", 2600, 0.0015, 5), ("Groundnut", 6300, 0.0, 4),
    ("Mustard", 5900, 0.0002, 4), ("Maize", 2250, 0.0001, 4),
]
DEMO_STATES = {
    "Maharashtra": (1.03, [("Pune", "Pune"), ("Nashik", "Lasalgaon"), ("Nagpur", "Nagpur")]),
    "Uttar Pradesh": (0.97, [("Lucknow", "Lucknow"), ("Kanpur", "Kanpur"), ("Agra", "Agra")]),
    "Madhya Pradesh": (0.98, [("Indore", "Indore"), ("Bhopal", "Bhopal"), ("Gwalior", "Gwalior")]),
    "Karnataka": (1.04, [("Bengaluru", "Bengaluru"), ("Dharwad", "Hubballi"), ("Mysuru", "Mysuru")]),
    "Tamil Nadu": (1.06, [("Chennai", "Koyambedu"), ("Coimbatore", "Coimbatore"), ("Madurai", "Madurai")]),
    "Punjab": (0.96, [("Ludhiana", "Ludhiana"), ("Amritsar", "Amritsar"), ("Jalandhar", "Jalandhar")]),
}


def seed_demo(db, days=60, today=None, seed=7):
    """Fill the database with clearly labelled synthetic prices so the app can be tried
    without an API key. The numbers are invented."""
    if db.execute("SELECT 1 FROM prices LIMIT 1").fetchone() and get_meta("demo_data") != "1":
        raise IngestError("The database already holds real price data; refusing to add demo data")
    db.execute("DELETE FROM prices")
    db.execute("DELETE FROM commodities")
    rng = random.Random(seed)
    today = today or date.today()
    states = list(DEMO_STATES)
    records = []
    for name, base, drift, n_states in DEMO_COMMODITIES:
        level, walk = float(base), []
        for _ in range(days):
            level *= 1 + drift + rng.gauss(0, 0.006)
            walk.append(level)
        for state in states[:n_states]:
            factor, markets = DEMO_STATES[state]
            for district, market in markets:
                market_factor = 1 + rng.uniform(-0.02, 0.02)
                for i, base_level in enumerate(walk):
                    day = today - timedelta(days=days - 1 - i)
                    modal = round(base_level * factor * market_factor * (1 + rng.gauss(0, 0.004)), -1)
                    records.append({
                        "date": day.isoformat(), "state": state, "district": district,
                        "market": market, "commodity": name, "variety": "Other",
                        "min_price": round(modal * 0.94, -1), "max_price": round(modal * 1.07, -1),
                        "modal_price": modal,
                    })
    written = upsert(db, records)
    set_meta("demo_data", "1")
    return written
