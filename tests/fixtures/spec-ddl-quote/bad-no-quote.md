## 6. Checkpoints

### CP3 — Trigger extension

Apply unchanged:

CREATE OR REPLACE FUNCTION communication_events_append_only()
RETURNS TRIGGER AS $$
BEGIN
END;
$$ LANGUAGE plpgsql;
