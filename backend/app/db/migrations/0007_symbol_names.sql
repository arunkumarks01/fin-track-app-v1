-- 0007_symbol_names — Phase 1c close-out (approved 2026-11-20).
--
-- 1. Renames: NSE lists a company under its name ON THAT DAY (IIFLWAM until 2023-01-23, then
--    360ONE). app.symbol_changes holds NSE's symbolchange.csv (static archive); the loader maps
--    each day's names to our current symbol, so pre-rename history becomes measurable.
-- 2. Trade-for-trade series: new listings trade their first sessions in BE (also relistings /
--    surveillance, BZ). The official row is EQ when present, else BE, else BZ.
-- 3. parse_version on the ledger: raising the loader's parser version re-fetches each older day
--    ONCE and inserts only rows that were missing — stored rows are never changed.

CREATE TABLE app.symbol_changes (
    old_symbol      text NOT NULL,
    new_symbol      text NOT NULL,
    effective_date  date NOT NULL,
    company         text NOT NULL,
    PRIMARY KEY (old_symbol, new_symbol, effective_date),
    CHECK (old_symbol <> new_symbol)
);

ALTER TABLE app.nse_bhav
    ADD COLUMN series        text NOT NULL DEFAULT 'EQ' CHECK (series IN ('EQ', 'BE', 'BZ')),
    ADD COLUMN source_symbol text;          -- NSE's symbol that day (NULL: the current one)

ALTER TABLE app.corporate_actions ADD COLUMN source_symbol text;

ALTER TABLE app.nse_archive_files
    ADD COLUMN parse_version smallint NOT NULL DEFAULT 1 CHECK (parse_version >= 1);
