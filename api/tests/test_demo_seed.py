"""Demo fleet seed v2 (Globe Plan C): retailer split, shape, tags, idempotency.

Runs against the throwaway mras_projector_test DB (projector_pool fixture).
Requires the dockerized Postgres running:
    cd /Users/jn/code/mras-ops && docker compose up -d postgres
"""
import pathlib
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("godview_isolate")

SEED = pathlib.Path(__file__).resolve().parents[2] / "db" / "seed" / "seed_demo_fleet.sql"
DEMO_ORG = "dea00000-0000-4000-8000-000000000001"  # umbrella (v1 name kept)
DEMO_RETAILERS = {
    "dea00000-0000-4000-8000-000000000002": "Northline Apparel",
    "dea00000-0000-4000-8000-000000000003": "Vantage Motors",
    "dea00000-0000-4000-8000-000000000004": "Corebrew Coffee",
    "dea00000-0000-4000-8000-000000000005": "Meridian Screens",
}
DEMO_ORG_UUIDS = [uuid.UUID(DEMO_ORG)] + [uuid.UUID(k) for k in DEMO_RETAILERS]
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
    assert 15 <= len(venues) <= 18
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
        "WHERE organization_id = ANY($1::uuid[]) GROUP BY location_id", DEMO_ORG_UUIDS)
    assert len(per_venue) >= 15
    assert all(2 <= r["n"] <= 5 for r in per_venue)

    shape = await projector_pool.fetch(
        """
        SELECT s.id,
               (SELECT count(*) FROM cameras c  WHERE c.system_id = s.id) AS cams,
               (SELECT count(*) FROM displays d WHERE d.system_id = s.id) AS disps,
               (SELECT count(*) FROM screen_groups g WHERE g.system_id = s.id) AS grps
        FROM systems s WHERE s.organization_id = ANY($1::uuid[])
        """, DEMO_ORG_UUIDS)
    for r in shape:
        assert 1 <= r["cams"] <= 2
        assert 2 <= r["disps"] <= 6
        assert r["grps"] == 1  # one screen_group per system (multi-display grouping)
    # systems tagged via config (systems has config jsonb, not metadata)
    untagged = await projector_pool.fetchval(
        "SELECT count(*) FROM systems WHERE organization_id = ANY($1::uuid[]) "
        "AND config->>'demo_seed' IS DISTINCT FROM 'true'", DEMO_ORG_UUIDS)
    assert untagged == 0
    # every device carries the demo- screen_id namespace and joins a demo system
    stray = await projector_pool.fetchval(
        "SELECT count(*) FROM cameras c JOIN systems s ON s.id = c.system_id "
        "WHERE s.organization_id = ANY($1::uuid[]) AND c.screen_id NOT LIKE 'demo-cam-%'", DEMO_ORG_UUIDS)
    assert stray == 0


async def test_seed_status_spread(projector_pool):
    await _apply_seed(projector_pool)
    disp = await projector_pool.fetch(
        "SELECT d.status::text AS st, count(*) AS n FROM displays d "
        "JOIN systems s ON s.id = d.system_id WHERE s.organization_id = ANY($1::uuid[]) "
        "GROUP BY d.status", DEMO_ORG_UUIDS)
    by = {r["st"]: r["n"] for r in disp}
    assert by.get("degraded", 0) >= 1
    assert by.get("offline", 0) >= 1
    assert by.get("active", 0) > by.get("degraded", 0) + by.get("offline", 0)  # mostly healthy
    cam_bad = await projector_pool.fetchval(
        "SELECT count(*) FROM cameras c JOIN systems s ON s.id = c.system_id "
        "WHERE s.organization_id = ANY($1::uuid[]) AND c.status IN ('degraded','offline')", DEMO_ORG_UUIDS)
    assert cam_bad >= 1


async def test_seed_idempotent(projector_pool):
    await _apply_seed(projector_pool)
    counts1 = await projector_pool.fetchrow(
        "SELECT (SELECT count(*) FROM locations) AS l, (SELECT count(*) FROM systems) AS s, "
        "(SELECT count(*) FROM cameras) AS c, (SELECT count(*) FROM displays) AS d, "
        "(SELECT count(*) FROM screen_groups) AS g, (SELECT count(*) FROM location_participants) AS lp, "
        "(SELECT count(*) FROM organizations) AS o, (SELECT count(*) FROM organization_relationships) AS orl")
    await _apply_seed(projector_pool)  # re-run must be a clean no-op
    counts2 = await projector_pool.fetchrow(
        "SELECT (SELECT count(*) FROM locations) AS l, (SELECT count(*) FROM systems) AS s, "
        "(SELECT count(*) FROM cameras) AS c, (SELECT count(*) FROM displays) AS d, "
        "(SELECT count(*) FROM screen_groups) AS g, (SELECT count(*) FROM location_participants) AS lp, "
        "(SELECT count(*) FROM organizations) AS o, (SELECT count(*) FROM organization_relationships) AS orl")
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


async def test_seed_retailer_orgs_linked_under_umbrella(projector_pool):
    await _apply_seed(projector_pool)
    # 'retailer' is not an organization_type value (010_enums.sql:12) — hosts.
    rows = await projector_pool.fetch(
        "SELECT id, name, organization_type::text AS t, parent_organization_id, "
        "metadata->>'demo_seed' AS tag FROM organizations WHERE id = ANY($1::uuid[]) "
        "AND id <> $2", DEMO_ORG_UUIDS, uuid.UUID(DEMO_ORG))
    assert {str(r["id"]): r["name"] for r in rows} == DEMO_RETAILERS
    for r in rows:
        assert r["t"] == "host"
        assert str(r["parent_organization_id"]) == DEMO_ORG
        assert r["tag"] == "true"
    links = await projector_pool.fetch(
        "SELECT from_organization_id, to_organization_id, relationship "
        "FROM organization_relationships WHERE from_organization_id = $1",
        uuid.UUID(DEMO_ORG))
    assert {str(l["to_organization_id"]) for l in links} == set(DEMO_RETAILERS)
    assert all(l["relationship"] == "umbrella" for l in links)


async def test_seed_split_one_retailer_per_venue_umbrella_empty(projector_pool):
    await _apply_seed(projector_pool)
    # umbrella owns ZERO systems after the split
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM systems WHERE organization_id = $1",
        uuid.UUID(DEMO_ORG)) == 0
    # each seeded venue is single-retailer by construction
    multi = await projector_pool.fetchval(
        "SELECT count(*) FROM (SELECT location_id FROM systems "
        "WHERE organization_id = ANY($1::uuid[]) "
        "GROUP BY location_id HAVING count(DISTINCT organization_id) > 1) x",
        DEMO_ORG_UUIDS)
    assert multi == 0
    # every retailer owns >=2 venues (Plan D arcs need a chain to draw)
    per_retailer = await projector_pool.fetch(
        "SELECT organization_id, count(DISTINCT location_id) AS venues "
        "FROM systems WHERE organization_id = ANY($1::uuid[]) GROUP BY organization_id",
        DEMO_ORG_UUIDS)
    assert len(per_retailer) == 4
    assert all(r["venues"] >= 2 for r in per_retailer)


async def test_seed_same_city_venue_pairs(projector_pool):
    # mras-ops #55: same-city venues so clustering triggers live. Cluster key is
    # city|country — strings must byte-match.
    await _apply_seed(projector_pool)
    pairs = await projector_pool.fetch(
        "SELECT city, country, count(*) AS n FROM locations "
        "WHERE metadata->>'demo_seed' = 'true' GROUP BY city, country "
        "HAVING count(*) >= 2")
    keys = {(r["city"], r["country"]) for r in pairs}
    assert {("New York", "US"), ("London", "GB"), ("Dubai", "AE")} <= keys


async def test_seed_v2_reassigns_systems_from_v1_umbrella_state(projector_pool):
    """THE v2-critical path: the live dev DB is v1-seeded (every demo system on
    the umbrella org). ON CONFLICT DO NOTHING cannot reassign — the explicit
    UPDATE must. Simulate v1 state, re-apply, assert the split lands."""
    await _apply_seed(projector_pool)
    await projector_pool.execute(
        "UPDATE systems SET organization_id = $1 WHERE config->>'demo_seed' = 'true'",
        uuid.UUID(DEMO_ORG))
    before = await projector_pool.fetchval("SELECT count(*) FROM systems")
    await _apply_seed(projector_pool)  # re-apply = reassign, never duplicate
    assert await projector_pool.fetchval("SELECT count(*) FROM systems") == before
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM systems WHERE organization_id = $1",
        uuid.UUID(DEMO_ORG)) == 0
    on_retailers = await projector_pool.fetchval(
        "SELECT count(*) FROM systems WHERE organization_id = ANY($1::uuid[]) "
        "AND organization_id <> $2", DEMO_ORG_UUIDS, uuid.UUID(DEMO_ORG))
    assert on_retailers == before
