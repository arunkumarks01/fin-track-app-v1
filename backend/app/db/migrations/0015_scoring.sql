-- A custom strategy is a revisioned, validated rule definition. Keeping older revisions
-- allows a backtest or a reported suggestion to refer to the rule set it actually evaluated.
CREATE TABLE app.scoring_strategies (
    id          text        NOT NULL,
    revision    integer     NOT NULL CHECK (revision > 0),
    created_at  timestamptz NOT NULL,
    enabled     boolean     NOT NULL,
    definition  jsonb       NOT NULL,
    PRIMARY KEY (id, revision)
);

-- Published by the backtest/calibration workflow after a held-out evaluation. The serving
-- path only reads published results, and only from the exact strategy revision and instrument.
CREATE TABLE app.scoring_calibrations (
    strategy_id         text        NOT NULL,
    strategy_revision   integer     NOT NULL CHECK (strategy_revision > 0),
    instrument          text        NOT NULL CHECK (instrument IN ('underlying', 'future', 'option')),
    model_key           text        NOT NULL CHECK (model_key ~ '^[a-f0-9]{64}$'),
    available_at        timestamptz NOT NULL,
    evaluated_through   timestamptz NOT NULL CHECK (evaluated_through < available_at),
    observations        integer     NOT NULL CHECK (observations > 0),
    wins                integer     NOT NULL CHECK (wins BETWEEN 0 AND observations),
    PRIMARY KEY (strategy_id, strategy_revision, instrument, model_key, available_at)
);

CREATE TABLE app.scoring_scans (
    mode        text        NOT NULL CHECK (mode IN ('swing', 'intraday')),
    as_of       timestamptz NOT NULL,
    report      jsonb       NOT NULL,
    PRIMARY KEY (mode, as_of)
);
