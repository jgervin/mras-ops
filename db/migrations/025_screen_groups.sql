-- 025_screen_groups.sql: first-class grouping of cameras/displays within a system.
-- Serves God View (Systems & Logs drill-down groups devices by wall/zone) and the
-- peel-back orchestration's open zone/area question (see handoff-03).
-- New enum defined here rather than in 010_enums.sql (010 is frozen / already applied).

CREATE TYPE screen_group_type AS ENUM ('zone', 'ad_cluster', 'custom');

CREATE TABLE screen_groups (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    system_id   uuid NOT NULL REFERENCES systems(id),
    location_id uuid REFERENCES locations(id),   -- denormalized, matches cameras/displays convention
    name        text NOT NULL,                    -- e.g. "Entrance Wall A"
    group_type  screen_group_type NOT NULL DEFAULT 'custom',
    status      lifecycle_status NOT NULL DEFAULT 'active',
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX screen_groups_system_idx ON screen_groups (system_id);

ALTER TABLE displays ADD COLUMN screen_group_id uuid REFERENCES screen_groups(id);
ALTER TABLE cameras  ADD COLUMN screen_group_id uuid REFERENCES screen_groups(id);
CREATE INDEX displays_screen_group_idx ON displays (screen_group_id);
CREATE INDEX cameras_screen_group_idx  ON cameras  (screen_group_id);
