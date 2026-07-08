"""TODO-8 Phase C (ops side): migration 027 + audited PATCH /cameras/{id}."""
import uuid

import pytest

from src.cameras import patch_camera  # add to imports at top

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def _org_loc_sys(pool, name="Sys1"):
    org, loc, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await pool.execute("INSERT INTO organizations (id,name,organization_type) VALUES ($1,'Org','advertiser')", org)
    await pool.execute("INSERT INTO locations (id,name,location_type) VALUES ($1,'Loc','store')", loc)
    await pool.execute("INSERT INTO systems (id,organization_id,location_id,name) VALUES ($1,$2,$3,$4)", sid, org, loc, name)
    return org, loc, sid


async def _camera(pool, sid, name="Cam1"):
    cid = uuid.uuid4()
    await pool.execute(
        "INSERT INTO cameras (id,system_id,name,screen_id) VALUES ($1,$2,$3,'scr_c1')",
        cid, sid, name)  # 3 placeholders, 3 args (outside-review fix M1)
    return cid


# --- migration 027 -----------------------------------------------------------

async def test_camera_role_enum_has_standby(projector_pool):
    rows = await projector_pool.fetch("SELECT unnest(enum_range(NULL::camera_role))::text AS v")
    assert "standby" in {r["v"] for r in rows}


async def test_failover_eligible_defaults_false(projector_pool):
    _, _, sid = await _org_loc_sys(projector_pool)
    cid = await _camera(projector_pool, sid)
    assert await projector_pool.fetchval(
        "SELECT failover_eligible FROM cameras WHERE id = $1", cid) is False


# --- patch_camera ------------------------------------------------------------

async def test_patch_updates_fields_and_returns_row(projector_pool):
    _, _, sid = await _org_loc_sys(projector_pool)
    cid = await _camera(projector_pool, sid)
    async with projector_pool.acquire() as conn:
        row = await patch_camera(conn, cid, {"camera_role": "standby", "failover_eligible": True})
    assert row["camera_role"] == "standby"
    assert row["failover_eligible"] is True
    assert row["status"] == "active"          # untouched field preserved
    assert row["name"] == "Cam1"              # identity never changes (spec §2)


async def test_patch_status_only(projector_pool):
    _, _, sid = await _org_loc_sys(projector_pool)
    cid = await _camera(projector_pool, sid)
    async with projector_pool.acquire() as conn:
        row = await patch_camera(conn, cid, {"status": "offline"})
    assert row["status"] == "offline"
    assert row["camera_role"] == "detection"  # role untouched (decision 12: offline != demote)


async def test_patch_journals_camera_admin_event(projector_pool):
    _, _, sid = await _org_loc_sys(projector_pool)
    cid = await _camera(projector_pool, sid)
    async with projector_pool.acquire() as conn:
        await patch_camera(conn, cid, {"camera_role": "standby"})
    ev = await projector_pool.fetchrow(
        "SELECT service, status, system_id, camera_id, payload FROM events "
        "WHERE event_type = 'camera_admin' ORDER BY id DESC LIMIT 1")
    assert ev is not None and ev["service"] == "mras-ops" and ev["status"] == "success"
    assert ev["camera_id"] == cid and ev["system_id"] == sid
    import json
    payload = json.loads(ev["payload"])
    assert payload["changes"]["camera_role"] == {"from": "detection", "to": "standby"}


async def test_patch_unknown_camera_returns_none_and_journals_nothing(projector_pool):
    async with projector_pool.acquire() as conn:
        assert await patch_camera(conn, uuid.uuid4(), {"camera_role": "standby"}) is None
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM events WHERE event_type = 'camera_admin'") == 0
