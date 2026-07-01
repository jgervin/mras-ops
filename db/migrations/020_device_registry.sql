-- 020_device_registry.sql: global screen_id uniqueness + unresolved-device audit table
--
-- MVP grain: GLOBAL uniqueness (not per-system) because the projector resolves from the
-- raw screen_id string alone — events carry no system_id at resolve time.
-- Revisit to UNIQUE(system_id, screen_id) only once events carry a site/system discriminator.
--
-- The new UNIQUE constraints supersede the plain lookup indexes from 012_physical.sql
-- (cameras_screen_id_idx, displays_screen_id_idx) — DROP those to avoid redundancy.

ALTER TABLE cameras  ADD CONSTRAINT cameras_screen_id_uniq  UNIQUE (screen_id);
ALTER TABLE displays ADD CONSTRAINT displays_screen_id_uniq UNIQUE (screen_id);

-- Dropped: these plain indexes are made redundant by the UNIQUE indexes above.
DROP INDEX IF EXISTS cameras_screen_id_idx;
DROP INDEX IF EXISTS displays_screen_id_idx;

-- Audit sink: records screen_ids that arrive in events before the device is registered.
-- event_id is bigint because events.id is bigserial (NOT uuid); a uuid FK would fail.
CREATE TABLE unresolved_devices (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    screen_id     text NOT NULL,
    kind          text NOT NULL CHECK (kind IN ('camera','display')),
    event_id      bigint REFERENCES events(id),   -- events.id is bigserial (NOT uuid)
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    seen_count    int NOT NULL DEFAULT 1,
    UNIQUE (screen_id, kind)
);
