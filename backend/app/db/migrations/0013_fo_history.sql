-- Slice 1f (DEC-1D-OWNERS, sources approved in advance by the user 2026-11-21): daily futures and
-- options history from NSE's F&O bhavcopy archive, 2015 onwards. One row per contract per day.
-- Only contracts that traded or hold open interest are stored (idle listed strikes are skipped).
-- Plain table: ~36M rows, ~4 GB with its index; the load stops at FO_DB_BUDGET_GB.
ALTER TABLE app.reference_files DROP CONSTRAINT reference_files_dataset_check;
ALTER TABLE app.reference_files ADD CONSTRAINT reference_files_dataset_check
    CHECK (dataset IN ('mto', 'participant_oi', 'participant_vol', 'bulk_deals', 'block_deals',
                       'pr_text', 'market_cap', 'fo_bhav'));

CREATE TABLE app.fo_daily (
    trade_date       date     NOT NULL,
    underlying       text     NOT NULL,            -- NSE's symbol that day (e.g. NIFTY, GVT&D)
    kind             text     NOT NULL CHECK (kind IN ('IDF', 'STF', 'IDO', 'STO', 'IVF')),
                                                    -- index/stock futures, index/stock options, VIX futures
    expiry           date     NOT NULL,
    strike_paisa     integer  NOT NULL CHECK (strike_paisa >= 0),   -- 0 for futures
    opt_type         text     NOT NULL CHECK (opt_type IN ('FUT', 'CE', 'PE')),
    open_paisa       integer  NOT NULL CHECK (open_paisa >= 0),
    high_paisa       integer  NOT NULL CHECK (high_paisa >= 0),
    low_paisa        integer  NOT NULL CHECK (low_paisa >= 0),
    close_paisa      integer  NOT NULL CHECK (close_paisa >= 0),
    settle_paisa     integer  NOT NULL CHECK (settle_paisa >= 0),
    prev_close_paisa integer  CHECK (prev_close_paisa >= 0),        -- new file format only (2024+)
    underlying_paisa integer  CHECK (underlying_paisa >= 0),        -- spot at the close, 2024+ only
    contracts        bigint   NOT NULL CHECK (contracts >= 0),
    open_interest    bigint   NOT NULL CHECK (open_interest >= 0),
    oi_change        bigint   NOT NULL,
    lot_size         integer  CHECK (lot_size > 0),                 -- 2024+ only
    PRIMARY KEY (trade_date, underlying, kind, expiry, strike_paisa, opt_type),
    CHECK ((opt_type = 'FUT') = (strike_paisa = 0 AND kind IN ('IDF', 'STF', 'IVF')))
);
CREATE INDEX fo_daily_underlying_idx ON app.fo_daily (underlying, trade_date DESC);
