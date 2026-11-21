CREATE TABLE IF NOT EXISTS commodities (
    slug TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

-- One row per market, commodity, variety and day. Prices are rupees per quintal (100 kg),
-- exactly as published by Agmarknet. The app converts to rupees per kg when displaying.
CREATE TABLE IF NOT EXISTS prices (
    id INTEGER PRIMARY KEY,
    price_date TEXT NOT NULL,
    state TEXT NOT NULL,
    district TEXT NOT NULL DEFAULT '',
    market TEXT NOT NULL,
    commodity TEXT NOT NULL,
    variety TEXT NOT NULL DEFAULT '',
    min_price REAL,
    max_price REAL,
    modal_price REAL NOT NULL,
    UNIQUE (price_date, state, market, commodity, variety)
);
CREATE INDEX IF NOT EXISTS idx_prices_commodity_date ON prices (commodity, price_date);
CREATE INDEX IF NOT EXISTS idx_prices_commodity_state_date ON prices (commodity, state, price_date);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro')),
    state TEXT NOT NULL DEFAULT '',
    telegram_chat_id TEXT NOT NULL DEFAULT '',
    digest_opt_in INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS basket_items (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    commodity TEXT NOT NULL,
    qty_kg REAL NOT NULL,
    UNIQUE (user_id, commodity)
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    commodity TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT '',
    direction TEXT NOT NULL CHECK (direction IN ('above', 'below')),
    threshold_per_kg REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    triggered INTEGER NOT NULL DEFAULT 0,
    last_fired_on TEXT
);

CREATE TABLE IF NOT EXISTS purchase_log (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    commodity TEXT NOT NULL,
    bought_on TEXT NOT NULL,
    price_per_kg REAL NOT NULL,
    qty_kg REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pro_requests (
    user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
    requested_at TEXT NOT NULL
);

-- People who only asked for the free weekly summary (no account).
CREATE TABLE IF NOT EXISTS subscribers (
    email TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

-- Recent login/signup attempts per client IP, for rate limiting (pruned as it goes).
CREATE TABLE IF NOT EXISTS auth_attempts (
    ip TEXT NOT NULL,
    ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_attempts_ip_ts ON auth_attempts (ip, ts);
