-- db/migrations/028_registry_admin_audit.sql
-- Fleet P1/P2 (spec D10 / §6): per-object audit trails are latest-N INDEX PROBES,
-- never journal scans. Same partial-expression pattern as 027's
-- events_camera_duty_idx. Additive only; safe under the test harness's
-- sorted-order apply. For the EXISTING dev DB apply manually/standalone
-- (initdb scripts only run on fresh volumes — same posture as 025/026/027):
--   cd /Users/jn/code/mras-ops && \
--   docker compose exec -T postgres psql -U mras -d mras < db/migrations/028_registry_admin_audit.sql

-- D10: the generic registry_admin event, keyed by payload object_id.
CREATE INDEX IF NOT EXISTS events_registry_admin_idx
    ON events ((payload->>'object_id'), id DESC)
    WHERE event_type = 'registry_admin';

-- The one legacy exception (D10): PATCH /cameras keeps emitting camera_admin.
-- GET /registry/audit merges it by payload camera_id — index it the same way so
-- the camera branch of the merge is also a probe (spec §6: never a journal scan).
CREATE INDEX IF NOT EXISTS events_camera_admin_idx
    ON events ((payload->>'camera_id'), id DESC)
    WHERE event_type = 'camera_admin';
