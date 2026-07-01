-- 019_projector_state.sql: singleton cursor table for the God View projector worker
-- id=1 singleton enforced by CHECK; ON CONFLICT seed is idempotent on replay.
-- cursor tracks the last consumed events.id (bigserial) so the worker resumes correctly.
CREATE TABLE projector_state (
    id            int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    cursor        bigint NOT NULL DEFAULT 0,
    last_event_ts timestamptz,
    updated_at    timestamptz NOT NULL DEFAULT now(),
    projector_ver text
);
INSERT INTO projector_state (id, cursor) VALUES (1, 0) ON CONFLICT (id) DO NOTHING;
