"""Demo fleet teardown (Globe Plan A): dependency-ordered delete leaves zero
demo rows, survives projector-shaped activity (back-stamped events.ad_run_id),
preserves real rows and the projector cursor.

Requires the dockerized Postgres running:
    cd /Users/jn/code/mras-ops && docker compose up -d postgres
"""
import json
import pathlib
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("godview_isolate")

BASE = pathlib.Path(__file__).resolve().parents[2] / "db" / "seed"
SEED = BASE / "seed_demo_fleet.sql"
TEARDOWN = BASE / "teardown_demo_fleet.sql"
DEMO_ORG = "dea00000-0000-4000-8000-000000000001"  # umbrella (v1 name kept)
DEMO_RETAILERS = {
    "dea00000-0000-4000-8000-000000000002": "Northline Apparel",
    "dea00000-0000-4000-8000-000000000003": "Vantage Motors",
    "dea00000-0000-4000-8000-000000000004": "Corebrew Coffee",
    "dea00000-0000-4000-8000-000000000005": "Meridian Screens",
}
DEMO_ORG_UUIDS = [uuid.UUID(DEMO_ORG)] + [uuid.UUID(k) for k in DEMO_RETAILERS]


async def _apply(pool, path):
    async with pool.acquire() as conn:
        await conn.execute(path.read_text())


async def _inject_demo_activity(pool):
    """Simulate v2 generator + projector output: activity attributed to a
    RETAILER org (never the umbrella — post-split the umbrella owns no systems),
    incl. an events row BACK-STAMPED with ad_run_id (the FK cycle) and an
    unresolved_devices row pointing at a demo event."""
    row = await pool.fetchrow(
        "SELECT s.id AS system_id, s.organization_id, s.location_id, d.screen_id "
        "FROM systems s JOIN displays d ON d.system_id = s.id "
        "WHERE s.organization_id = ANY($1::uuid[]) LIMIT 1", DEMO_ORG_UUIDS)
    assert row is not None and str(row["organization_id"]) != DEMO_ORG
    trig = uuid.uuid4()
    comp_id = await pool.fetchval(
        "INSERT INTO composition_runs (trigger_id, organization_id, location_id, system_id, status) "
        "VALUES ($1,$2,$3,$4,'rendered') RETURNING id",
        trig, row["organization_id"], row["location_id"], row["system_id"])
    ad_run_id = await pool.fetchval(
        "INSERT INTO ad_runs (trigger_id, organization_id, location_id, system_id, "
        "composition_run_id, status) VALUES ($1,$2,$3,$4,$5,'completed') RETURNING id",
        trig, row["organization_id"], row["location_id"], row["system_id"], comp_id)
    await pool.execute(
        "INSERT INTO playbacks (trigger_id, screen_id, ad_run_id, organization_id, "
        "location_id, system_id, status) VALUES ($1,$2,$3,$4,$5,$6,'ended')",
        trig, row["screen_id"], ad_run_id, row["organization_id"],
        row["location_id"], row["system_id"])
    event_id = await pool.fetchval(
        "INSERT INTO events (trigger_id, service, event_type, status, payload, "
        "organization_id, location_id, system_id, ad_run_id) "
        "VALUES ($1,'mras-composer','ad_run','completed',$2::jsonb,$3,$4,$5,$6) RETURNING id",
        trig, json.dumps({"demo_seed": True, "screen_id": row["screen_id"],
                          "screen_kind": "display"}),
        row["organization_id"], row["location_id"], row["system_id"], ad_run_id)
    await pool.execute(
        "INSERT INTO unresolved_devices (screen_id, kind, event_id) VALUES ($1,'display',$2)",
        "demo-disp-never-registered", event_id)
    return trig


async def test_teardown_leaves_zero_demo_rows(projector_pool):
    await _apply(projector_pool, SEED)
    await _inject_demo_activity(projector_pool)
    await _apply(projector_pool, TEARDOWN)

    checks = {
        "organizations": "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
        "locations": ("SELECT count(*) FROM locations WHERE metadata->>'demo_seed' = 'true'", None),
        "location_participants": "SELECT count(*) FROM location_participants WHERE organization_id = ANY($1::uuid[])",
        "systems": "SELECT count(*) FROM systems WHERE organization_id = ANY($1::uuid[])",
        "ad_runs": "SELECT count(*) FROM ad_runs WHERE organization_id = ANY($1::uuid[])",
        "composition_runs": "SELECT count(*) FROM composition_runs WHERE organization_id = ANY($1::uuid[])",
        "playbacks": "SELECT count(*) FROM playbacks WHERE organization_id = ANY($1::uuid[])",
        "events": "SELECT count(*) FROM events WHERE organization_id = ANY($1::uuid[])",
    }
    for name, q in checks.items():
        if isinstance(q, tuple):
            n = await projector_pool.fetchval(q[0])
        else:
            n = await projector_pool.fetchval(q, DEMO_ORG_UUIDS)
        assert n == 0, f"{name} still has demo rows"
    # devices/groups are gone (their parent systems are gone; count all —
    # godview_isolate started us from a clean slate)
    for table in ("cameras", "displays", "screen_groups"):
        assert await projector_pool.fetchval(f"SELECT count(*) FROM {table}") == 0, table
    # events with demo payload tag are gone too (belt-and-braces clause)
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM events WHERE payload->>'demo_seed' = 'true'") == 0


async def test_teardown_preserves_real_rows_and_cursor(projector_pool):
    # a "real" org/location/system + activity that must survive
    real_org, real_loc, real_sys = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO organizations (id,name,organization_type) VALUES ($1,'Demo Org','host')", real_org)
    await projector_pool.execute(
        "INSERT INTO locations (id,name,location_type) VALUES ($1,'Demo Store','store')", real_loc)
    await projector_pool.execute(
        "INSERT INTO systems (id,organization_id,location_id,name) VALUES ($1,$2,$3,'Demo System')",
        real_sys, real_org, real_loc)
    await projector_pool.execute(
        "INSERT INTO ad_runs (trigger_id,organization_id,system_id,status) VALUES ($1,$2,$3,'completed')",
        uuid.uuid4(), real_org, real_sys)
    await projector_pool.execute("UPDATE projector_state SET cursor = 4242 WHERE id = 1")

    await _apply(projector_pool, SEED)
    await _apply(projector_pool, TEARDOWN)

    assert await projector_pool.fetchval(
        "SELECT count(*) FROM organizations WHERE id = $1", real_org) == 1
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM systems WHERE id = $1", real_sys) == 1
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM ad_runs WHERE organization_id = $1", real_org) == 1
    # NEVER touch the projector cursor (spec §5)
    assert await projector_pool.fetchval(
        "SELECT cursor FROM projector_state WHERE id = 1") == 4242


async def test_teardown_idempotent(projector_pool):
    await _apply(projector_pool, SEED)
    await _apply(projector_pool, TEARDOWN)
    await _apply(projector_pool, TEARDOWN)  # second run: clean no-op, no errors
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", DEMO_ORG_UUIDS) == 0


async def test_teardown_removes_relationships_and_all_five_orgs(projector_pool):
    await _apply(projector_pool, SEED)
    await _apply(projector_pool, TEARDOWN)
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM organization_relationships "
        "WHERE from_organization_id = ANY($1::uuid[]) "
        "   OR to_organization_id = ANY($1::uuid[])", DEMO_ORG_UUIDS) == 0
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
        DEMO_ORG_UUIDS) == 0
