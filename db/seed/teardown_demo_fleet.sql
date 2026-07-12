-- /Users/jn/code/mras-ops/db/seed/teardown_demo_fleet.sql
-- Reverses /Users/jn/code/mras-ops/db/seed/seed_demo_fleet.sql (v2: umbrella +
-- 4 retailer orgs) plus everything the demo-traffic generator + projector
-- derived from it (spec 2026-07-12 §3).
-- Apply manually (stop scripts/demo_traffic.py first):
--   docker exec -i mras-ops-postgres-1 psql -U mras -d mras < db/seed/teardown_demo_fleet.sql
--
-- V2: scope is the DEMO-ORG-ID SET below — v1 hardcoded the umbrella uuid in
-- 28 predicates; ANY single-uuid predicate left behind silently strands
-- retailer-attributed rows. EVERY org predicate reads from demo_org_ids.
-- EXPLICIT DEPENDENCY ORDER (no ON DELETE CASCADE anywhere):
--   * events.ad_run_id -> ad_runs is BACK-STAMPED by the projector fold —
--     NULL it for demo rows before deleting ad_runs (FK cycle via
--     personalization_decisions).
--   * unresolved_devices.event_id -> events — NULL it for demo events.
--   * organization_relationships rows go BEFORE the orgs (both FK columns
--     NOT NULL, no cascade); retailer orgs BEFORE the umbrella (their
--     parent_organization_id references it).
-- NEVER reset projector_state / never "rebuild" the projector as cleanup.
-- LEAVE-AS-IS SET: the real "Demo Org" (55bf0abd-...), "Demo System"
-- (d8d2d05d-...), and "Demo Store" (acc4e851-...) rows — including the lat/lng
-- the seed gave Demo Store — are intentionally untouched.
-- Idempotent: every statement is scoped; re-running deletes nothing.

BEGIN;

CREATE TEMP TABLE demo_org_ids ON COMMIT DROP AS
SELECT unnest(ARRAY[
    'dea00000-0000-4000-8000-000000000001',  -- Demo Retail Group (umbrella)
    'dea00000-0000-4000-8000-000000000002',  -- Northline Apparel
    'dea00000-0000-4000-8000-000000000003',  -- Vantage Motors
    'dea00000-0000-4000-8000-000000000004',  -- Corebrew Coffee
    'dea00000-0000-4000-8000-000000000005'   -- Meridian Screens
]::uuid[]) AS id;

-- 0. Cycle breakers
UPDATE events SET ad_run_id = NULL
WHERE ad_run_id IN (
    SELECT id FROM ad_runs
    WHERE organization_id IN (SELECT id FROM demo_org_ids)
       OR system_id IN (SELECT id FROM systems
                        WHERE organization_id IN (SELECT id FROM demo_org_ids)));
UPDATE unresolved_devices SET event_id = NULL
WHERE event_id IN (SELECT id FROM events
                   WHERE organization_id IN (SELECT id FROM demo_org_ids)
                      OR payload->>'demo_seed' = 'true');

-- 1. Projector-derived activity leaves. Scoped by org AND by demo systems
-- (belt-and-braces: a row folded before back-stamping still carries the demo
-- system scope).
DELETE FROM viewer_exposures
WHERE organization_id IN (SELECT id FROM demo_org_ids)
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM identity_matches
WHERE subject_observation_id IN (
    SELECT id FROM subject_observations
    WHERE organization_id IN (SELECT id FROM demo_org_ids)
       OR system_id IN (SELECT id FROM systems
                        WHERE organization_id IN (SELECT id FROM demo_org_ids)));
DELETE FROM playbacks
WHERE organization_id IN (SELECT id FROM demo_org_ids)
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));

-- 2. Runs (children before parents: ad_runs -> composition_runs -> decisions)
DELETE FROM ad_runs
WHERE organization_id IN (SELECT id FROM demo_org_ids)
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM composition_runs
WHERE organization_id IN (SELECT id FROM demo_org_ids)
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM personalization_decisions
WHERE organization_id IN (SELECT id FROM demo_org_ids)
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM subject_observations
WHERE organization_id IN (SELECT id FROM demo_org_ids)
   OR system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));

-- 3. Journal rows (payload clause is belt-and-braces for rows that escaped
-- org-stamping).
DELETE FROM events
WHERE organization_id IN (SELECT id FROM demo_org_ids)
   OR payload->>'demo_seed' = 'true';

-- 4. Remaining derived rows keyed on demo devices/systems
DELETE FROM observation_tracks
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids))
   OR camera_screen_id LIKE 'demo-cam-%';
DELETE FROM device_health_events
WHERE device_id IN (SELECT id FROM devices
                    WHERE system_id IN (SELECT id FROM systems
                                        WHERE organization_id IN (SELECT id FROM demo_org_ids)));
DELETE FROM system_health_events
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));

-- 5. Devices -> groups -> systems -> participants -> orphaned venues ->
--    org links -> retailer orgs -> umbrella
DELETE FROM cameras
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM displays
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM screen_groups
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM devices
WHERE system_id IN (SELECT id FROM systems
                    WHERE organization_id IN (SELECT id FROM demo_org_ids));
DELETE FROM systems
WHERE organization_id IN (SELECT id FROM demo_org_ids);
DELETE FROM location_participants
WHERE organization_id IN (SELECT id FROM demo_org_ids);
-- Only ORPHANED seeded venues — and never Demo Store (it is untagged).
DELETE FROM locations
WHERE metadata->>'demo_seed' = 'true'
  AND NOT EXISTS (SELECT 1 FROM systems s WHERE s.location_id = locations.id);
-- Org links first (both FK columns NOT NULL -> organizations, no cascade) …
DELETE FROM organization_relationships
WHERE from_organization_id IN (SELECT id FROM demo_org_ids)
   OR to_organization_id   IN (SELECT id FROM demo_org_ids);
-- … then retailers (parent_organization_id -> umbrella) …
DELETE FROM organizations
WHERE id IN (SELECT id FROM demo_org_ids)
  AND id <> 'dea00000-0000-4000-8000-000000000001';
-- … then the umbrella.
DELETE FROM organizations
WHERE id = 'dea00000-0000-4000-8000-000000000001';

COMMIT;
