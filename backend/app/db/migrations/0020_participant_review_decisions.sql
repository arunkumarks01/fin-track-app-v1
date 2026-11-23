-- Record reproducible source checks without replacing NSE's published numbers.
CREATE TABLE app.participant_review_decisions (
    dataset text NOT NULL,
    trade_date date NOT NULL,
    source_sha256 text NOT NULL,
    method text NOT NULL,
    outcome text NOT NULL CHECK (outcome IN (
        'reconciled_within_tolerance', 'component_totals_disagree',
        'component_sums_disagree', 'source_date_unverified'
    )),
    tolerance_contracts numeric NOT NULL CHECK (tolerance_contracts >= 0),
    detail jsonb NOT NULL,
    reviewed_at timestamptz NOT NULL,
    PRIMARY KEY (dataset, trade_date, source_sha256, method),
    FOREIGN KEY (dataset, trade_date, source_sha256)
      REFERENCES app.participant_exact_imports (dataset, trade_date, source_sha256)
);
