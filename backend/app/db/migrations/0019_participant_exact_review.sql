-- Exact NSE participant observations, held separately from verified integer positions.
CREATE TABLE app.participant_exact_imports (
    dataset text NOT NULL,
    trade_date date NOT NULL,
    source_sha256 text NOT NULL,
    date_status text NOT NULL CHECK (date_status IN ('verified','unverified','invalid','mismatch','conflicting')),
    fractional boolean NOT NULL,
    source_differences jsonb NOT NULL,
    quality_status text NOT NULL DEFAULT 'review_required' CHECK (quality_status = 'review_required'),
    imported_at timestamptz NOT NULL,
    PRIMARY KEY (dataset, trade_date, source_sha256),
    FOREIGN KEY (dataset,trade_date,source_sha256)
      REFERENCES app.participant_source_review (dataset,trade_date,source_sha256)
);

CREATE TABLE app.participant_exact_rows (
    dataset text NOT NULL,
    trade_date date NOT NULL,
    source_sha256 text NOT NULL,
    client_type text NOT NULL CHECK (client_type IN ('Client','DII','FII','Pro','TOTAL')),
    -- Ordered as app.data.nse_reference.FIELDS; NUMERIC preserves all reported decimals.
    values_exact numeric[] NOT NULL CHECK (cardinality(values_exact) = 14),
    PRIMARY KEY (dataset, trade_date, source_sha256, client_type),
    FOREIGN KEY (dataset,trade_date,source_sha256)
      REFERENCES app.participant_exact_imports (dataset,trade_date,source_sha256)
);
