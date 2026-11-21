-- Phase 1e steps 2-7 (DEC-1E-SOURCES): deals, announcements, board meetings, news, economic
-- calendar and FRED series. All plain tables (small). Nothing here is ever back-filled from a
-- source other than the one named on the row.

-- the ledger also tracks the forward-only deal files and the PR-zip text members
ALTER TABLE app.reference_files DROP CONSTRAINT reference_files_dataset_check;
ALTER TABLE app.reference_files ADD CONSTRAINT reference_files_dataset_check
    CHECK (dataset IN ('mto', 'participant_oi', 'participant_vol', 'bulk_deals', 'block_deals',
                       'pr_text'));

-- NSE bulk / block deals (content/equities/bulk.csv, block.csv): the latest session only, so
-- they are captured forward; a session never captured stays 'missing' in the ledger.
-- Every listed security is kept (a few hundred rows a day), not only the universe.
CREATE TABLE app.deals (
    trade_date    date    NOT NULL,
    kind          text    NOT NULL CHECK (kind IN ('bulk', 'block')),
    symbol        text    NOT NULL,                 -- NSE:SYMBOL as NSE printed it that day
    security_name text    NOT NULL,
    client        text    NOT NULL,
    side          text    NOT NULL CHECK (side IN ('BUY', 'SELL')),
    quantity      bigint  NOT NULL CHECK (quantity > 0),
    price_paisa   bigint  NOT NULL CHECK (price_paisa > 0),
    -- a weighted-average price can have more decimals than a paisa: rounded half up, flagged
    price_exact   boolean NOT NULL,
    remarks       text,
    PRIMARY KEY (trade_date, kind, symbol, client, side, quantity, price_paisa)
);
CREATE INDEX deals_symbol_idx ON app.deals (symbol, trade_date DESC);

-- Company announcements (PR zip member an*.txt): the headline text only, universe shares only.
CREATE TABLE app.announcements (
    id            bigserial PRIMARY KEY,
    symbol        text  NOT NULL,                   -- NSE:SYMBOL (today's name)
    announced_on  date  NOT NULL,                   -- the trading date of the file
    category      text,                             -- NSE's subject, when the format carries one
    body          text  NOT NULL,
    source_symbol text                              -- NSE's name that day, when it differs
);
CREATE UNIQUE INDEX announcements_dedupe
    ON app.announcements (symbol, announced_on, md5(coalesce(category, '') || '|' || body));
CREATE INDEX announcements_symbol_idx ON app.announcements (symbol, announced_on DESC);

-- Board meetings (PR zip member bm*.txt): the scheduled date and purpose; results dates come
-- from here. A meeting announced again on a later day is one row (first seen kept).
CREATE TABLE app.board_meetings (
    id            bigserial PRIMARY KEY,
    symbol        text    NOT NULL,
    meeting_date  date    NOT NULL,
    purpose       text    NOT NULL,
    is_results    boolean NOT NULL,                 -- purpose mentions financial results
    first_seen    date    NOT NULL,
    source_symbol text
);
CREATE UNIQUE INDEX board_meetings_dedupe
    ON app.board_meetings (symbol, meeting_date, md5(purpose));
CREATE INDEX board_meetings_upcoming_idx ON app.board_meetings (meeting_date);

-- News headlines from publisher RSS (headline, link, summary only — no full-text scraping).
CREATE TABLE app.news_feeds (
    source          text        PRIMARY KEY,        -- config name, e.g. 'mint_markets'
    url             text        NOT NULL,
    last_status     text        CHECK (last_status IN ('ok', 'error', 'refused')),
    last_detail     text,
    last_fetched_at timestamptz,
    items_last      integer     NOT NULL DEFAULT 0,
    ttl_minutes     integer     CHECK (ttl_minutes IS NULL OR ttl_minutes >= 0)  -- the feed's <ttl>
);
CREATE TABLE app.news_items (
    id           bigserial   PRIMARY KEY,
    source       text        NOT NULL REFERENCES app.news_feeds (source),
    link         text        NOT NULL UNIQUE,
    title        text        NOT NULL,
    summary      text,
    published_at timestamptz,                       -- NULL when the feed gives no parseable date
    fetched_at   timestamptz NOT NULL
);
CREATE INDEX news_items_recent_idx ON app.news_items (coalesce(published_at, fetched_at) DESC);

-- Economic calendar: imported from a reviewed CSV, every row with the URL it came from.
CREATE TABLE app.economic_events (
    id          bigserial   PRIMARY KEY,
    event_date  date        NOT NULL,
    event_time  time,                               -- local time in `timezone`, when known
    timezone    text        NOT NULL DEFAULT 'Asia/Kolkata',
    country     text        NOT NULL CHECK (country ~ '^[A-Z]{2}$'),
    kind        text        NOT NULL,               -- e.g. rbi_policy, cpi, gdp, fomc
    title       text        NOT NULL,
    source_url  text        NOT NULL CHECK (source_url ~ '^https://'),
    imported_at timestamptz NOT NULL,
    UNIQUE (event_date, country, kind, title)
);

-- FRED (St. Louis Fed) daily series: values as scaled integers (value × 10^scale), the unit kept.
CREATE TABLE app.macro_series_meta (
    series_id       text        PRIMARY KEY,
    title           text        NOT NULL,
    unit            text        NOT NULL,
    scale           smallint    NOT NULL CHECK (scale BETWEEN 0 AND 6),
    last_status     text        CHECK (last_status IN ('ok', 'error', 'refused')),
    last_detail     text,
    last_fetched_at timestamptz
);
CREATE TABLE app.macro_series (
    series_id    text   NOT NULL REFERENCES app.macro_series_meta (series_id),
    obs_date     date   NOT NULL,
    value_scaled bigint NOT NULL,
    PRIMARY KEY (series_id, obs_date)
);
