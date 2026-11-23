-- Prevent a late writer from recreating a tick range already removed after export.
-- Retention remains disabled until export, locking, and crash recovery are proven together.
CREATE INDEX tick_exports_dropped_range_idx ON app.tick_exports (chunk_start, chunk_end)
    WHERE dropped_at IS NOT NULL;

CREATE FUNCTION app.reject_archived_tick_range() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM app.tick_exports
        WHERE dropped_at IS NOT NULL
          AND chunk_start <= NEW.ts AND NEW.ts < chunk_end
    ) THEN
        RAISE EXCEPTION 'tick timestamp belongs to an archived and dropped range'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER ticks_reject_archived_range
BEFORE INSERT OR UPDATE OF ts ON app.ticks
FOR EACH ROW EXECUTE FUNCTION app.reject_archived_tick_range();
