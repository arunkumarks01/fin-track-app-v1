-- Phase 1e step 1 (DEC-1E-SOURCES): delivery % and FII/DII derivatives positioning, both from
-- NSE's static archive, 2015 onwards. Plain tables (small: ~660k + ~24k rows).

-- One row per (dataset, trading date): resumable loads + honest gaps, like app.nse_archive_files.
CREATE TABLE app.reference_files (
    dataset        text        NOT NULL CHECK (dataset IN ('mto', 'participant_oi',
                                                           'participant_vol')),
    trade_date     date        NOT NULL,
    status         text        NOT NULL CHECK (status IN ('ok', 'missing', 'error')),
    rows_stored    integer     NOT NULL DEFAULT 0 CHECK (rows_stored >= 0),
    rejected       integer     NOT NULL DEFAULT 0 CHECK (rejected >= 0),
    detail         text,
    fetched_at     timestamptz NOT NULL,
    parse_version  smallint    NOT NULL DEFAULT 1 CHECK (parse_version >= 1),
    PRIMARY KEY (dataset, trade_date)
);

-- NSE's security-wise delivery position (MTO_ddmmyyyy.DAT), universe shares only.
-- deliv_bps is computed from the two quantities (integer basis points, half up) and checked
-- against NSE's printed percentage; quantities are shares, as traded that day.
CREATE TABLE app.delivery_daily (
    symbol          text     NOT NULL,              -- NSE:SYMBOL (today's name)
    trade_date      date     NOT NULL,
    series          text     NOT NULL CHECK (series IN ('EQ', 'BE', 'BZ')),
    traded_qty      bigint   NOT NULL CHECK (traded_qty > 0),
    deliverable_qty bigint   NOT NULL CHECK (deliverable_qty >= 0
                                             AND deliverable_qty <= traded_qty),
    deliv_bps       integer  NOT NULL CHECK (deliv_bps BETWEEN 0 AND 10000),
    source_symbol   text,                            -- NSE's name that day, when it differs
    PRIMARY KEY (symbol, trade_date)
);

-- Participant-wise open interest / trading volume in equity derivatives (no. of contracts).
-- Only the four participant rows are stored; the file's TOTAL row is checked, not kept.
CREATE TABLE app.participant_positions (
    trade_date          date   NOT NULL,
    kind                text   NOT NULL CHECK (kind IN ('oi', 'volume')),
    client_type         text   NOT NULL CHECK (client_type IN ('Client', 'DII', 'FII', 'Pro')),
    fut_idx_long        bigint NOT NULL CHECK (fut_idx_long >= 0),
    fut_idx_short       bigint NOT NULL CHECK (fut_idx_short >= 0),
    fut_stk_long        bigint NOT NULL CHECK (fut_stk_long >= 0),
    fut_stk_short       bigint NOT NULL CHECK (fut_stk_short >= 0),
    opt_idx_call_long   bigint NOT NULL CHECK (opt_idx_call_long >= 0),
    opt_idx_put_long    bigint NOT NULL CHECK (opt_idx_put_long >= 0),
    opt_idx_call_short  bigint NOT NULL CHECK (opt_idx_call_short >= 0),
    opt_idx_put_short   bigint NOT NULL CHECK (opt_idx_put_short >= 0),
    opt_stk_call_long   bigint NOT NULL CHECK (opt_stk_call_long >= 0),
    opt_stk_put_long    bigint NOT NULL CHECK (opt_stk_put_long >= 0),
    opt_stk_call_short  bigint NOT NULL CHECK (opt_stk_call_short >= 0),
    opt_stk_put_short   bigint NOT NULL CHECK (opt_stk_put_short >= 0),
    total_long          bigint NOT NULL CHECK (total_long >= 0),
    total_short         bigint NOT NULL CHECK (total_short >= 0),
    PRIMARY KEY (trade_date, kind, client_type)
);
