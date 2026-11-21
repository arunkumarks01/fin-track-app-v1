-- NSE security-level market capitalisation and published issue size (DEC-1E-SOURCES).
ALTER TABLE app.reference_files DROP CONSTRAINT reference_files_dataset_check;
ALTER TABLE app.reference_files ADD CONSTRAINT reference_files_dataset_check
    CHECK (dataset IN ('mto', 'participant_oi', 'participant_vol', 'bulk_deals', 'block_deals',
                       'pr_text', 'market_cap'));

CREATE TABLE app.market_cap_daily (
    symbol           text   NOT NULL,
    trade_date       date   NOT NULL,
    series           text   NOT NULL CHECK (series IN ('EQ', 'BE', 'BZ')),
    issue_size       bigint NOT NULL CHECK (issue_size > 0),
    market_cap_paisa bigint NOT NULL CHECK (market_cap_paisa > 0),
    source_symbol    text,
    PRIMARY KEY (symbol, trade_date)
);
