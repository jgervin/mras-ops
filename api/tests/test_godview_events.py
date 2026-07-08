"""God View unified health-event log, keyset paginated newest-first."""
import uuid

import pytest

from src.godview.events import get_events

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def _system(pool, name="Sys1"):
    org, loc, sid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await pool.execute("INSERT INTO organizations (id,name,organization_type) VALUES ($1,'Org','advertiser')", org)
    await pool.execute("INSERT INTO locations (id,name,location_type) VALUES ($1,'Loc','store')", loc)
    await pool.execute("INSERT INTO systems (id,organization_id,location_id,name) VALUES ($1,$2,$3,$4)", sid, org, loc, name)
    return sid


async def test_unifies_and_serializes_detail_string(projector_pool):
    sid = await _system(projector_pool)
    await projector_pool.execute(
        "INSERT INTO system_health_events (system_id,status,detail,observed_at) "
        "VALUES ($1,'degraded', '{\"message\":\"cpu high\"}'::jsonb, now())", sid)
    page = await get_events(projector_pool)
    assert page["items"][0]["kind"] == "system"
    assert page["items"][0]["ref_name"] == "Sys1"
    assert page["items"][0]["detail"] == "cpu high"


async def test_keyset_orders_newest_first_no_overlap(projector_pool):
    sid = await _system(projector_pool)
    for i in range(3):
        await projector_pool.execute(
            "INSERT INTO system_health_events (system_id,status,detail,observed_at) "
            "VALUES ($1,'active', '{}'::jsonb, now() - make_interval(secs => $2))", sid, i)
    p1 = await get_events(projector_pool, limit=2)
    assert len(p1["items"]) == 2
    assert p1["next_cursor"] is not None
    # newest first: item0.observed_at >= item1.observed_at
    assert p1["items"][0]["observed_at"] >= p1["items"][1]["observed_at"]
    p2 = await get_events(projector_pool, limit=2, cursor=p1["next_cursor"])
    ids1 = {str(i["id"]) for i in p1["items"]}
    ids2 = {str(i["id"]) for i in p2["items"]}
    assert ids1.isdisjoint(ids2)
    assert len(p2["items"]) == 1
