"""GET /registry/audit (D10 merge of registry_admin/camera_admin/camera_duty)
and GET /unresolved-devices (D11 read half)."""
import json
import uuid

import pytest

from src.registry.reads import get_audit, list_unresolved
from tests.registry_seed import unresolved

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def _event(pool, event_type, payload):
    await pool.execute(
        "INSERT INTO events (trigger_id, service, event_type, status, payload) "
        "VALUES (gen_random_uuid(), 'mras-ops', $1, 'success', $2::jsonb)",
        event_type, json.dumps(payload))


async def test_audit_merges_three_event_types_newest_first(projector_pool):
    oid = str(uuid.uuid4())
    await _event(projector_pool, "camera_duty",
                 {"camera_id": oid, "from": "standby", "to": "watching"})
    await _event(projector_pool, "camera_admin",
                 {"camera_id": oid, "changes": {"failover_eligible": {"from": False, "to": True}}})
    await _event(projector_pool, "registry_admin",
                 {"object_type": "camera", "object_id": oid, "action": "update",
                  "changes": {"name": {"from": "Old", "to": "New"}}})
    await _event(projector_pool, "registry_admin",
                 {"object_type": "camera", "object_id": str(uuid.uuid4()),  # other object
                  "action": "update", "changes": {}})
    trail = await get_audit(projector_pool, oid)
    assert [e["event_type"] for e in trail["items"]] == [
        "registry_admin", "camera_admin", "camera_duty"]
    assert trail["items"][0]["payload"]["changes"]["name"]["to"] == "New"   # payload as object
    assert trail["next_cursor"] is None


async def test_audit_keyset_by_event_id(projector_pool):
    oid = str(uuid.uuid4())
    for n in range(3):
        await _event(projector_pool, "registry_admin",
                     {"object_type": "camera", "object_id": oid, "action": "update",
                      "changes": {"name": {"from": str(n), "to": str(n + 1)}}})
    p1 = await get_audit(projector_pool, oid, limit=2)
    assert len(p1["items"]) == 2 and p1["next_cursor"] is not None
    p2 = await get_audit(projector_pool, oid, cursor=p1["next_cursor"], limit=2)
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None
    assert p1["items"][0]["id"] > p1["items"][1]["id"] > p2["items"][0]["id"]


async def test_unresolved_devices_list(projector_pool):
    await unresolved(projector_pool, screen_id="display-9", kind="display", seen_count=3)
    await unresolved(projector_pool, screen_id="scr_ghost", kind="camera", seen_count=1)
    page = await list_unresolved(projector_pool)
    assert page["counts"]["total"] == 2
    ids = {i["screen_id"] for i in page["items"]}
    assert ids == {"display-9", "scr_ghost"}
    assert all("seen_count" in i and "first_seen_at" in i for i in page["items"])
