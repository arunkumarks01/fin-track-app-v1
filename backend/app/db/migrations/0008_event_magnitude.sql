-- 0008_event_magnitude — Admin review list: material steps vs refit noise (2026-11-21).
--
-- Unexplained adjustment steps get their measured size as a column (it was only inside the
-- detail text), so the Admin review list can separate material steps from refit noise.
-- New analysis runs write it directly; existing rows are back-filled ONCE from the detail
-- text, which FinTrack itself writes in two fixed formats ("off by X%" and
-- "with no NSE action (X%)"; X has 2 decimals, so back-filled sizes are 1-bp resolution).
-- Explained / attributed / official_mismatch steps have no size (NULL).

ALTER TABLE app.adjustment_events ADD COLUMN magnitude numeric;

UPDATE app.adjustment_events
   SET magnitude = coalesce(
           substring(detail FROM 'off by ([0-9.]+)%'),
           substring(detail FROM 'with no NSE action \(([0-9.]+)%\)')
       )::numeric / 100
 WHERE classification IN ('unexplained_small', 'suspect');
