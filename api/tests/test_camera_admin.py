"""TODO-8 Phase C (ops side): migration 027 + audited PATCH /cameras/{id}."""
import uuid

import pytest

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
