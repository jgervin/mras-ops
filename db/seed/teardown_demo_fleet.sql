-- /Users/jn/code/mras-ops/db/seed/teardown_demo_fleet.sql
-- Reverses /Users/jn/code/mras-ops/db/seed/seed_demo_fleet.sql plus everything
-- the demo-traffic generator + projector derived from it (spec 2026-07-11 §5).
-- Apply manually (stop scripts/demo_traffic.py first):
--   docker exec -i mras-ops-postgres-1 psql -U mras -d mras < db/seed/teardown_demo_fleet.sql
--
-- EXPLICIT DEPENDENCY ORDER (no ON DELETE CASCADE exists anywhere in the schema).
-- Two FK cycle-breakers the spec's enumeration needs (recon 2026-07-12):
--   * events.ad_run_id -> ad_runs is BACK-STAMPED by the projector fold, while
--     personalization_decisions.event_id -> events and
--     ad_runs.personalization_decision_id -> personalization_decisions close a
--     cycle — NULL events.ad_run_id for demo rows before deleting ad_runs.
--   * unresolved_devices.event_id -> events — NULL it for demo events.
-- NEVER reset projector_state / never "rebuild" the projector as cleanup.
-- LEAVE-AS-IS SET: the real "Demo Org" (55bf0abd-...), "Demo System"
-- (d8d2d05d-...), and "Demo Store" (acc4e851-...) rows — including the lat/lng
-- the seed gave Demo Store — are intentionally untouched.
-- Idempotent: every statement is scoped; re-running deletes nothing.

BEGIN;

-- 0. Cycle breakers
UPDATE events SET ad_run_id = NULL
WHERE ad_run_id IN (
    SELECT id FROM ad_runs
    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
       OR system_id IN (SELECT id FROM systems
                        WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'));
UPDATE unresolved_devices SET event_id = NULL
WHERE event_id IN (SELECT id FROM events
                   WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
                      OR payload->>'demo_seed' = 'true');

-- 1. Projector-derived activity leaves (children of everything else). Scoped by
-- org AND by demo systems (belt-and-braces: a row folded before back-stamping
-- would still carry the demo system scope).
DELETE FROM viewer_exposures
WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');
DELETE FROM identity_matches
WHERE subject_observation_id IN (
    SELECT id FROM subject_observations
    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
       OR system_id IN (SELECT id FROM systems
                        WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'));
DELETE FROM playbacks
WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');

-- 2. Runs (children before parents: ad_runs -> composition_runs -> decisions)
DELETE FROM ad_runs
WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');
DELETE FROM composition_runs
WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');
DELETE FROM personalization_decisions
WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');
DELETE FROM subject_observations
WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');

-- 3. Journal rows (children of ad_runs are gone / nulled; subject_observations
-- and personalization_decisions — the two tables that FK INTO events — are gone).
-- payload clause is belt-and-braces for any row that escaped org-stamping.
DELETE FROM events
WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'
   OR payload->>'demo_seed' = 'true';

-- 4. Remaining derived rows keyed on demo devices/systems
DELETE FROM observation_tracks
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001')
   OR camera_screen_id LIKE 'demo-cam-%';
DELETE FROM device_health_events
WHERE device_id IN (SELECT id FROM devices
                    WHERE system_id IN (SELECT id FROM systems
                                        WHERE organization_id = 'dea00000-0000-4000-8000-000000000001'));
DELETE FROM system_health_events
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');

-- 5. Devices -> groups -> systems -> participants -> orphaned venues -> org
DELETE FROM cameras
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');
DELETE FROM displays
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');
DELETE FROM screen_groups
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');
DELETE FROM devices
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id = 'dea00000-0000-4000-8000-000000000001');
DELETE FROM systems
WHERE organization_id = 'dea00000-0000-4000-8000-000000000001';
DELETE FROM location_participants
WHERE organization_id = 'dea00000-0000-4000-8000-000000000001';
-- Only ORPHANED seeded venues (never a tagged location some other system moved
-- into) — and never Demo Store (it is untagged).
DELETE FROM locations
WHERE metadata->>'demo_seed' = 'true'
  AND NOT EXISTS (SELECT 1 FROM systems s WHERE s.location_id = locations.id);
DELETE FROM organizations
WHERE id = 'dea00000-0000-4000-8000-000000000001';

COMMIT;
