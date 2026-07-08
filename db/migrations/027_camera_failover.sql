-- db/migrations/027_camera_failover.sql
-- TODO-8 Phase C: multi-camera roles & failover (spec decisions 9 & 13). Additive only.
--
-- ALTER TYPE ... ADD VALUE is legal inside a transaction on PG >= 12 ONLY IF the new
-- value is not used in the same transaction — this file never uses 'standby', so it is
-- safe under both initdb and the test harness's single execute(). For the EXISTING dev
-- DB apply it manually/standalone (initdb scripts only run on fresh volumes — same
-- posture as 025/026); command in the plan's Task 5.
ALTER TYPE camera_role ADD VALUE IF NOT EXISTS 'standby';

-- Decision 9: explicit failover eligibility; default false = no behavior change.
ALTER TABLE cameras ADD COLUMN IF NOT EXISTS failover_eligible boolean NOT NULL DEFAULT false;

-- Serves God View effective_duty: latest camera_duty event per camera. Partial +
-- expression index — NOTE: the first expression index in this schema (023 is
-- partial-on-(ts) precedent for the partial part only). Only duty *transitions*
-- are indexed (rare rows), so it stays tiny while keeping the per-camera
-- latest-event probe off the unbounded append-only events heap.
CREATE INDEX IF NOT EXISTS events_camera_duty_idx
    ON events ((payload->>'camera_id'), id DESC)
    WHERE event_type = 'camera_duty';
