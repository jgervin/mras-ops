-- /Users/jn/code/mras-ops/db/seed/seed_demo_fleet.sql
-- God View Globe Plan A (spec 2026-07-11 §5): "Demo Retail Group" fake fleet.
-- Seeds live OUTSIDE db/migrations/ on purpose — initdb applies migrations on
-- fresh volumes and fake data must never bake into every fresh DB.
-- Apply manually:
--   docker exec -i mras-ops-postgres-1 psql -U mras -d mras < db/seed/seed_demo_fleet.sql
-- Idempotent: fixed/derived uuids + ON CONFLICT DO NOTHING; re-running is a no-op.
-- Reverse with /Users/jn/code/mras-ops/db/seed/teardown_demo_fleet.sql.
-- Scope tags: org id dea00000-0000-4000-8000-000000000001 is primary;
-- metadata.demo_seed=true on org/locations/location_participants/screen_groups;
-- systems carry it in config (systems has no metadata column); cameras/displays
-- have neither (calibration only) — they are identified by the demo- screen_id
-- namespace + their system join.

BEGIN;

INSERT INTO organizations (id, name, organization_type, status, metadata)
VALUES ('dea00000-0000-4000-8000-000000000001', 'Demo Retail Group', 'host',
        'active', '{"demo_seed": true}')
ON CONFLICT DO NOTHING;

-- Venue catalog: 13 venues, real coordinates, US/EU/APAC; malls + one airport
-- + one showroom (location_type 'store'). n_systems drives the 2-5 spread.
CREATE TEMP TABLE demo_venues (
    slug text, name text, ltype text, city text, country text, tz text,
    lat numeric, lng numeric, n_systems int
) ON COMMIT DROP;
INSERT INTO demo_venues VALUES
  ('moa',      'Mall of America',                'mall',    'Bloomington',     'US', 'America/Chicago',      44.8549,  -93.2422, 4),
  ('kop',      'King of Prussia Mall',           'mall',    'King of Prussia', 'US', 'America/New_York',     40.0885,  -75.3946, 3),
  ('century',  'Westfield Century City',         'mall',    'Los Angeles',     'US', 'America/Los_Angeles',  34.0584, -118.4173, 3),
  ('aventura', 'Aventura Mall',                  'mall',    'Aventura',        'US', 'America/New_York',     25.9565,  -80.1428, 2),
  ('yorkdale', 'Yorkdale Shopping Centre',       'mall',    'Toronto',         'CA', 'America/Toronto',      43.7255,  -79.4522, 2),
  ('wlondon',  'Westfield London',               'mall',    'London',          'GB', 'Europe/London',        51.5079,   -0.2216, 4),
  ('berlin',   'Mall of Berlin',                 'mall',    'Berlin',          'DE', 'Europe/Berlin',        52.5100,   13.3805, 2),
  ('partdieu', 'Westfield La Part-Dieu',         'mall',    'Lyon',            'FR', 'Europe/Paris',         45.7610,    4.8570, 2),
  ('dubai',    'The Dubai Mall',                 'mall',    'Dubai',           'AE', 'Asia/Dubai',           25.1972,   55.2796, 5),
  ('siam',     'Siam Paragon',                   'mall',    'Bangkok',         'TH', 'Asia/Bangkok',         13.7462,  100.5347, 2),
  ('chadstone','Chadstone Shopping Centre',      'mall',    'Melbourne',       'AU', 'Australia/Melbourne', -37.8859,  145.0838, 3),
  ('changi',   'Changi Airport Terminal 3',      'airport', 'Singapore',       'SG', 'Asia/Singapore',        1.3554,  103.9866, 4),
  ('fifthave', 'Fifth Avenue Flagship Showroom', 'store',   'New York',        'US', 'America/New_York',     40.7638,  -73.9730, 2);

INSERT INTO locations (id, name, location_type, city, country, lat, lng, timezone, status, metadata)
SELECT md5('demo-fleet:loc:' || slug)::uuid, name, ltype::location_type,
       city, country, lat, lng, tz, 'active', '{"demo_seed": true}'
FROM demo_venues
ON CONFLICT DO NOTHING;

INSERT INTO location_participants (id, location_id, organization_id, role, status, metadata)
SELECT md5('demo-fleet:lp:' || slug)::uuid, md5('demo-fleet:loc:' || slug)::uuid,
       'dea00000-0000-4000-8000-000000000001', 'host', 'active', '{"demo_seed": true}'
FROM demo_venues
ON CONFLICT DO NOTHING;

-- Systems: n_systems per venue, concept-doc style names, deterministic ids.
CREATE TEMP TABLE demo_systems ON COMMIT DROP AS
SELECT md5('demo-fleet:sys:' || v.slug || ':' || i)::uuid          AS id,
       md5('demo-fleet:loc:' || v.slug)::uuid                     AS location_id,
       v.slug                                                     AS slug,
       i                                                          AS sys_idx,
       (ARRAY['Entrance Wall A','Food Court Wall','Atrium Displays',
              'Concourse Screens','Promenade Wall'])[i]           AS name,
       (ARRAY['Level 1','Food Court','Atrium','Concourse B','Promenade'])[i] AS zone
FROM demo_venues v, generate_series(1, v.n_systems) AS i;

INSERT INTO systems (id, organization_id, location_id, name, system_type, zone, status, config)
SELECT id, 'dea00000-0000-4000-8000-000000000001', location_id, name,
       'onsite_mras', zone, 'active', '{"demo_seed": true}'
FROM demo_systems
ON CONFLICT DO NOTHING;

-- One screen_group per system (spec: screen_groups where multi-display; every
-- demo system has >=2 displays).
INSERT INTO screen_groups (id, system_id, location_id, name, group_type, status, metadata)
SELECT md5('demo-fleet:grp:' || slug || ':' || sys_idx)::uuid, id, location_id,
       name || ' Group', 'zone', 'active', '{"demo_seed": true}'
FROM demo_systems
ON CONFLICT DO NOTHING;

-- Cameras: 1-2 per system (1 + sys_idx % 2). screen_id is globally UNIQUE (020)
-- so the demo- namespace guarantees no collision with real devices
-- ('screen_0', 'display-2', ...).
INSERT INTO cameras (id, system_id, location_id, screen_group_id, name,
                     camera_role, screen_id, status, last_seen_at)
SELECT md5('demo-fleet:cam:' || s.slug || ':' || s.sys_idx || ':' || c)::uuid,
       s.id, s.location_id,
       md5('demo-fleet:grp:' || s.slug || ':' || s.sys_idx)::uuid,
       s.name || ' Cam ' || c, 'detection',
       'demo-cam-' || s.slug || '-' || s.sys_idx || '-' || c,
       'active', now()
FROM demo_systems s, generate_series(1, 1 + s.sys_idx % 2) AS c
ON CONFLICT DO NOTHING;

-- Displays: 2-6 per system (2 + (sys_idx*3) % 5).
INSERT INTO displays (id, system_id, location_id, screen_group_id, name,
                      screen_id, display_role, status, last_seen_at)
SELECT md5('demo-fleet:disp:' || s.slug || ':' || s.sys_idx || ':' || d)::uuid,
       s.id, s.location_id,
       md5('demo-fleet:grp:' || s.slug || ':' || s.sys_idx)::uuid,
       s.name || ' Display ' || d,
       'demo-disp-' || s.slug || '-' || s.sys_idx || '-' || d,
       'primary_ad', 'active', now()
FROM demo_systems s, generate_series(1, 2 + (s.sys_idx * 3) % 5) AS d
ON CONFLICT DO NOTHING;

-- Status spread so Health mode has something to show (mostly healthy, a couple
-- of warning/offline devices). Deterministic screen_ids; idempotent UPDATEs.
UPDATE displays SET status = 'degraded'
WHERE screen_id IN ('demo-disp-wlondon-2-1', 'demo-disp-moa-3-2', 'demo-disp-dubai-4-1');
UPDATE displays SET status = 'offline', last_seen_at = now() - interval '3 hours'
WHERE screen_id = 'demo-disp-changi-1-2';
UPDATE cameras SET status = 'degraded'
WHERE screen_id = 'demo-cam-century-2-1';
UPDATE cameras SET status = 'offline', last_seen_at = now() - interval '5 hours'
WHERE screen_id = 'demo-cam-kop-1-1';

-- Real demo box gets coordinates so it plots as the one live dot (spec §5).
-- GUARDED single-row UPDATE: locations.id acc4e851-ab7a-4b59-989e-85cb8b597e14
-- is the dev DB's "Demo Store" (verified live 2026-07-12). If the dev DB was
-- rebuilt since, re-check the id before applying. Coordinates are the demo rig's
-- site — placeholder San Francisco; adjust the literals if the rig moves.
-- "lat IS NULL" guard: never clobbers owner-set coordinates; re-runs are no-ops.
-- Teardown intentionally leaves this row untouched ("leave as-is" set).
UPDATE locations
SET lat = 37.7749, lng = -122.4194,
    city    = COALESCE(NULLIF(city, ''), 'San Francisco'),
    country = COALESCE(NULLIF(country, ''), 'US'),
    updated_at = now()
WHERE id = 'acc4e851-ab7a-4b59-989e-85cb8b597e14' AND lat IS NULL;

COMMIT;
