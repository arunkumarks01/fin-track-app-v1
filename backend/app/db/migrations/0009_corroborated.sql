-- DEC-CORROB (2026-11-21): an unlisted step that NSE's own prices prove is a clean split/bonus
-- is classified 'corroborated' (data kept as measured, always on the Admin review list).
ALTER TABLE app.adjustment_events DROP CONSTRAINT adjustment_events_classification_check;
ALTER TABLE app.adjustment_events ADD CONSTRAINT adjustment_events_classification_check
    CHECK (classification IN ('explained', 'attributed', 'unexplained_small', 'suspect',
                              'corroborated', 'official_mismatch'));
