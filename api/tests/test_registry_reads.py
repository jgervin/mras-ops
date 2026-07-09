"""Fleet P1 lists: parent-scoped, keyset, counts (spec §5.2 P1, D9)."""
import pytest

from src.registry.reads import list_locations, list_organizations
from tests.registry_seed import child_location, org_loc_sys, root_location

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def test_locations_root_level_scopes_and_counts(projector_pool):
    _, loc, _ = await org_loc_sys(projector_pool)               # root location + 1 system
    await child_location(projector_pool, loc, name="Zone A")
    page = await list_locations(projector_pool, parent_id=None)  # "root"
    assert page["counts"]["total"] == 1                          # the child is NOT at root level
    (item,) = page["items"]
    assert item["id"] == loc
    assert item["location_type"] == "store"
    assert item["status"] == "active"
    assert item["child_location_count"] == 1
    assert item["system_count"] == 1
    assert page["next_cursor"] is None


async def test_locations_child_level(projector_pool):
    _, loc, _ = await org_loc_sys(projector_pool)
    await child_location(projector_pool, loc, name="Zone A")
    page = await list_locations(projector_pool, parent_id=loc)
    assert [i["name"] for i in page["items"]] == ["Zone A"]
    assert page["items"][0]["child_location_count"] == 0
    assert page["items"][0]["system_count"] == 0


async def test_locations_keyset_pagination(projector_pool):
    for n in ("Aaa", "Bbb", "Ccc"):
        await root_location(projector_pool, name=n)
    p1 = await list_locations(projector_pool, parent_id=None, limit=2)
    assert [i["name"] for i in p1["items"]] == ["Aaa", "Bbb"]
    assert p1["counts"]["total"] == 3
    p2 = await list_locations(projector_pool, parent_id=None, limit=2, cursor=p1["next_cursor"])
    assert [i["name"] for i in p2["items"]] == ["Ccc"]
    assert p2["next_cursor"] is None


async def test_organizations_list(projector_pool):
    await org_loc_sys(projector_pool, org_name="Acme")
    page = await list_organizations(projector_pool)
    assert page["counts"]["total"] == 1
    (item,) = page["items"]
    assert item["name"] == "Acme"
    assert item["organization_type"] == "advertiser"
    assert item["parent_organization_id"] is None


from src.registry.reads import list_cameras, list_displays, list_screen_groups, list_systems
from tests.registry_seed import camera, display, screen_group


async def test_systems_scoped_by_location_with_device_count(projector_pool):
    _, loc, sid = await org_loc_sys(projector_pool)
    await camera(projector_pool, sid, screen_id="scr_c1")
    await display(projector_pool, sid, screen_id="scr_d1")
    other_loc = await root_location(projector_pool, name="Elsewhere")
    page = await list_systems(projector_pool, location_id=loc)
    assert page["counts"]["total"] == 1
    (item,) = page["items"]
    assert item["system_type"] == "onsite_mras"
    assert item["device_count"] == 2
    empty = await list_systems(projector_pool, location_id=other_loc)
    assert empty["counts"]["total"] == 0 and empty["items"] == []


async def test_screen_groups_scoped_by_system(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    gid = await screen_group(projector_pool, sid, name="North Wall")
    await camera(projector_pool, sid, screen_id="scr_c1", group=gid)
    page = await list_screen_groups(projector_pool, system_id=sid)
    (item,) = page["items"]
    assert item["name"] == "North Wall"
    assert item["group_type"] == "custom"
    assert item["device_count"] == 1


async def test_cameras_by_system_and_by_group_with_duty(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    gid = await screen_group(projector_pool, sid)
    cid = await camera(projector_pool, sid, name="Cam A", screen_id="scr_c1", group=gid)
    await camera(projector_pool, sid, name="Cam B", screen_id="scr_c2")
    await projector_pool.execute(
        "INSERT INTO events (trigger_id, service, event_type, status, payload) "
        "VALUES (gen_random_uuid(), 'vision', 'camera_duty', 'success', "
        "        jsonb_build_object('camera_id', $1::text, 'from', 'standby', 'to', 'watching'))",
        str(cid))
    by_sys = await list_cameras(projector_pool, system_id=sid)
    assert by_sys["counts"]["total"] == 2
    cam_a = next(i for i in by_sys["items"] if i["name"] == "Cam A")
    assert cam_a["effective_duty"] == "watching"       # duty probe (027 index)
    assert cam_a["failover_eligible"] is False
    assert cam_a["screen_group_id"] == gid
    cam_b = next(i for i in by_sys["items"] if i["name"] == "Cam B")
    assert cam_b["effective_duty"] == "unknown"
    by_group = await list_cameras(projector_pool, screen_group_id=gid)
    assert [i["name"] for i in by_group["items"]] == ["Cam A"]


async def test_displays_by_system_and_group(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    gid = await screen_group(projector_pool, sid)
    await display(projector_pool, sid, name="Kiosk 1", screen_id="display-1", group=gid)
    await display(projector_pool, sid, name="Kiosk 2", screen_id="display-2")
    by_sys = await list_displays(projector_pool, system_id=sid)
    assert by_sys["counts"]["total"] == 2
    k1 = next(i for i in by_sys["items"] if i["name"] == "Kiosk 1")
    assert k1["display_role"] == "primary_ad" and k1["screen_id"] == "display-1"
    by_group = await list_displays(projector_pool, screen_group_id=gid)
    assert [i["name"] for i in by_group["items"]] == ["Kiosk 1"]


async def test_device_lists_paginate_with_null_names(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    await camera(projector_pool, sid, name=None, screen_id="scr_z1")   # NULL name sorts first
    await camera(projector_pool, sid, name="Aaa", screen_id="scr_z2")
    await camera(projector_pool, sid, name="Bbb", screen_id="scr_z3")
    p1 = await list_cameras(projector_pool, system_id=sid, limit=2)
    assert [i["name"] for i in p1["items"]] == [None, "Aaa"]
    p2 = await list_cameras(projector_pool, system_id=sid, limit=2, cursor=p1["next_cursor"])
    assert [i["name"] for i in p2["items"]] == ["Bbb"]
