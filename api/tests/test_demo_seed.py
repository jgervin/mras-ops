"""Demo fleet seed (Globe Plan A): shape, tags, idempotency.

Runs against the throwaway mras_projector_test DB (projector_pool fixture).
Requires the dockerized Postgres running:
    cd /Users/jn/code/mras-ops && docker compose up -d postgres
"""
import pathlib

import pytest

pytestmark = pytest.mark.usefixtures("godview_isolate")

SEED = pathlib.Path(__file__).resolve().parents[2] / "db" / "seed" / "seed_demo_fleet.sql"
DEMO_ORG = "dea00000-0000-4000-8000-000000000001"
REAL_DEMO_LOCATION = "acc4e851-ab7a-4b59-989e-85cb8b597e14"  # dev-DB "Demo Store"


async def _apply_seed(pool):
    async with pool.acquire() as conn:
        await conn.execute(SEED.read_text())


async def test_seed_creates_tagged_org_and_venues(projector_pool):
    await _apply_seed(projector_pool)
    org = await projector_pool.fetchrow(
        "SELECT name, organization_type::text AS t, metadata->>'demo_seed' AS tag "
        "FROM organizations WHERE id = $1", DEMO_ORG)
    assert org is not None
    assert org["name"] == "Demo Retail Group"
    assert org["t"] == "host"
    assert org["tag"] == "true"

    venues = await projector_pool.fetch(
        "SELECT id, location_type::text AS lt, lat, lng, city, country, timezone "
        "FROM locations WHERE metadata->>'demo_seed' = 'true'")
    assert 12 <= len(venues) <= 15
    # every seeded venue plots: real lat/lng + city/country/timezone present
    for v in venues:
        assert v["lat"] is not None and v["lng"] is not None
        assert v["city"] and v["country"] and v["timezone"]
    types = {v["lt"] for v in venues}
    assert "mall" in types and "airport" in types and "store" in types


async def test_seed_fleet_shape_per_venue_and_system(projector_pool):
    await _apply_seed(projector_pool)
    per_venue = await projector_pool.fetch(
        "SELECT location_id, count(*) AS n FROM systems "
        "WHERE organization_id = $1 GROUP BY location_id", DEMO_ORG)
    assert len(per_venue) >= 12
    assert all(2 <= r["n"] <= 5 for r in per_venue)

    shape = await projector_pool.fetch(
        """
        SELECT s.id,
               (SELECT count(*) FROM cameras c  WHERE c.system_id = s.id) AS cams,
               (SELECT count(*) FROM displays d WHERE d.system_id = s.id) AS disps,
               (SELECT count(*) FROM screen_groups g WHERE g.system_id = s.id) AS grps
        FROM systems s WHERE s.organization_id = $1
        """, DEMO_ORG)
    for r in shape:
        assert 1 <= r["cams"] <= 2
        assert 2 <= r["disps"] <= 6
        assert r["grps"] == 1  # one screen_group per system (multi-display grouping)
    # systems tagged via config (systems has config jsonb, not metadata)
    untagged = await projector_pool.fetchval(
        "SELECT count(*) FROM systems WHERE organization_id = $1 "
        "AND config->>'demo_seed' IS DISTINCT FROM 'true'", DEMO_ORG)
    assert untagged == 0
    # every device carries the demo- screen_id namespace and joins a demo system
    stray = await projector_pool.fetchval(
        "SELECT count(*) FROM cameras c JOIN systems s ON s.id = c.system_id "
        "WHERE s.organization_id = $1 AND c.screen_id NOT LIKE 'demo-cam-%'", DEMO_ORG)
    assert stray == 0


async def test_seed_status_spread(projector_pool):
    await _apply_seed(projector_pool)
    disp = await projector_pool.fetch(
        "SELECT d.status::text AS st, count(*) AS n FROM displays d "
        "JOIN systems s ON s.id = d.system_id WHERE s.organization_id = $1 "
        "GROUP BY d.status", DEMO_ORG)
    by = {r["st"]: r["n"] for r in disp}
    assert by.get("degraded", 0) >= 1
    assert by.get("offline", 0) >= 1
    assert by.get("active", 0) > by.get("degraded", 0) + by.get("offline", 0)  # mostly healthy
    cam_bad = await projector_pool.fetchval(
        "SELECT count(*) FROM cameras c JOIN systems s ON s.id = c.system_id "
        "WHERE s.organization_id = $1 AND c.status IN ('degraded','offline')", DEMO_ORG)
    assert cam_bad >= 1


async def test_seed_idempotent(projector_pool):
    await _apply_seed(projector_pool)
    counts1 = await projector_pool.fetchrow(
        "SELECT (SELECT count(*) FROM locations) AS l, (SELECT count(*) FROM systems) AS s, "
        "(SELECT count(*) FROM cameras) AS c, (SELECT count(*) FROM displays) AS d, "
        "(SELECT count(*) FROM screen_groups) AS g, (SELECT count(*) FROM location_participants) AS lp")
    await _apply_seed(projector_pool)  # re-run must be a clean no-op
    counts2 = await projector_pool.fetchrow(
        "SELECT (SELECT count(*) FROM locations) AS l, (SELECT count(*) FROM systems) AS s, "
        "(SELECT count(*) FROM cameras) AS c, (SELECT count(*) FROM displays) AS d, "
        "(SELECT count(*) FROM screen_groups) AS g, (SELECT count(*) FROM location_participants) AS lp")
    assert dict(counts1) == dict(counts2)


async def test_seed_guarded_update_is_scoped_to_real_demo_location(projector_pool):
    # In the throwaway DB the real demo location id does not exist: the guarded
    # UPDATE must update zero rows and never invent it or touch other locations.
    await projector_pool.execute(
        "INSERT INTO locations (id, name, location_type) VALUES "
        "('11111111-1111-4111-8111-111111111111', 'Innocent Bystander', 'store')")
    await _apply_seed(projector_pool)
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM locations WHERE id = $1", REAL_DEMO_LOCATION) == 0
    row = await projector_pool.fetchrow(
        "SELECT lat, lng FROM locations WHERE id = '11111111-1111-4111-8111-111111111111'")
    assert row["lat"] is None and row["lng"] is None
