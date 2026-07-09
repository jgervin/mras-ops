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
