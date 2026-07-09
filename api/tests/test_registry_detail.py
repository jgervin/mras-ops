"""GET /{type}/{id} detail: identity/config/state split per spec §5.1 (D1/D2)."""
import pytest

from src.registry.reads import get_detail
from tests.registry_seed import camera, display, org_loc_sys, screen_group

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def test_camera_detail_splits_identity_config_state(projector_pool):
    _, loc, sid = await org_loc_sys(projector_pool)
    gid = await screen_group(projector_pool, sid)
    cid = await camera(projector_pool, sid, name="Cam A", screen_id="scr_c1", group=gid)
    d = await get_detail(projector_pool, "camera", cid)
    assert d["object_type"] == "camera"
    assert d["identity"] == {
        "id": str(cid), "device_id": None, "system_id": str(sid),
        "location_id": None, "screen_id": "scr_c1",
    }   # location_id: seed inserts cameras without the denormalized location (nullable, 012)
    assert d["config"]["name"] == "Cam A"
    assert d["config"]["camera_role"] == "detection"
    assert d["config"]["failover_eligible"] is False
    assert d["config"]["screen_group_id"] == str(gid)
    assert d["config"]["calibration"] == {}            # jsonb parsed to an object
    assert d["config"]["status"] == "active"           # status lives in config (D2 status*)
    assert d["state"]["effective_duty"] == "unknown"
    assert d["state"]["last_seen_at"] is None
    assert "created_at" in d["state"] and "updated_at" in d["state"]
    assert "status" not in d["state"] and "name" not in d["identity"]


async def test_display_and_group_and_container_details(projector_pool):
    org, loc, sid = await org_loc_sys(projector_pool)
    gid = await screen_group(projector_pool, sid, name="Wall")
    did = await display(projector_pool, sid, name="Kiosk 1", screen_id="display-1")
    disp = await get_detail(projector_pool, "display", did)
    assert disp["identity"]["screen_id"] == "display-1"
    assert disp["config"]["display_role"] == "primary_ad"
    assert disp["config"]["resolution_width"] is None
    assert "effective_duty" not in disp["state"]

    grp = await get_detail(projector_pool, "screen_group", gid)
    assert grp["identity"] == {"id": str(gid), "system_id": str(sid), "location_id": None}
    assert grp["config"]["group_type"] == "custom" and grp["config"]["metadata"] == {}

    system = await get_detail(projector_pool, "system", sid)
    assert system["identity"] == {"id": str(sid), "organization_id": str(org), "location_id": str(loc)}
    assert system["config"]["system_type"] == "onsite_mras" and system["config"]["config"] == {}

    location = await get_detail(projector_pool, "location", loc)
    assert location["identity"] == {"id": str(loc), "parent_location_id": None}
    assert location["config"]["location_type"] == "store" and location["config"]["lat"] is None

    organization = await get_detail(projector_pool, "organization", org)
    assert organization["identity"] == {"id": str(org), "parent_organization_id": None}
    assert organization["config"]["organization_type"] == "advertiser"


async def test_camera_detail_reports_latest_duty(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    cid = await camera(projector_pool, sid, screen_id="scr_c1")
    await projector_pool.execute(
        "INSERT INTO events (trigger_id, service, event_type, status, payload) "
        "VALUES (gen_random_uuid(), 'vision', 'camera_duty', 'success', "
        "        jsonb_build_object('camera_id', $1::text, 'from', 'standby', 'to', 'watching'))",
        str(cid))
    d = await get_detail(projector_pool, "camera", cid)
    assert d["state"]["effective_duty"] == "watching"


async def test_unknown_id_returns_none(projector_pool):
    import uuid
    assert await get_detail(projector_pool, "camera", uuid.uuid4()) is None
