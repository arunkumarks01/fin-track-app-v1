-- Preserve the original NSE participant source when it fails strict validation.
-- These files remain excluded from clean participant positions until reviewed.
CREATE TABLE app.participant_source_review (
    dataset text NOT NULL CHECK (dataset IN ('participant_oi', 'participant_vol')),
    trade_date date NOT NULL,
    source_url text NOT NULL,
    source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[0-9a-f]{64}$'),
    source_bytes bytea NOT NULL,
    validation_error text NOT NULL,
    captured_at timestamptz NOT NULL,
    PRIMARY KEY (dataset, trade_date, source_sha256)
);
