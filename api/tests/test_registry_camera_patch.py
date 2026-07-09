"""PATCH /cameras extended to full §5.1 camera config (D2/D3/D6/D10), plus the
I-1 orchestrator amendment (2026-07-08, BINDING): journaled changes are
filtered to from != to; a zero-effective-change PATCH skips the event."""
import json
import uuid

import pytest

from src.cameras import patch_camera
from src.registry.lifecycle import TransitionError
from src.registry.writes import SemanticError
from tests.registry_seed import camera, org_loc_sys, screen_group

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def test_patch_name_and_group_and_journals_camera_admin(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    gid = await screen_group(projector_pool, sid)
    cid = await camera(projector_pool, sid, name="Cam1", screen_id="scr_c1")
    async with projector_pool.acquire() as conn:
        row = await patch_camera(conn, cid, {"name": "Entrance Cam", "screen_group_id": gid,
                                             "stream_url": "rtsp://x/1",
                                             "calibration": {"cam_index": 2}})
    assert row["name"] == "Entrance Cam"
    assert row["screen_group_id"] == gid
    assert row["stream_url"] == "rtsp://x/1"
    assert json.loads(row["calibration"]) == {"cam_index": 2}
    ev = await projector_pool.fetchrow(
        "SELECT payload FROM events WHERE event_type = 'camera_admin' ORDER BY id DESC LIMIT 1")
    changes = json.loads(ev["payload"])["changes"]        # legacy event, keys additive (D10)
    assert changes["name"] == {"from": "Cam1", "to": "Entrance Cam"}
    assert changes["screen_group_id"]["to"] == str(gid)


async def test_patch_ungroup_sets_null(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    gid = await screen_group(projector_pool, sid)
    cid = await camera(projector_pool, sid, screen_id="scr_c1", group=gid)
    async with projector_pool.acquire() as conn:
        row = await patch_camera(conn, cid, {"screen_group_id": None})
    assert row["screen_group_id"] is None


async def test_patch_cross_system_group_rejected(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    _, _, other = await org_loc_sys(projector_pool, sys_name="Sys2")
    foreign_gid = await screen_group(projector_pool, other)
    cid = await camera(projector_pool, sid, screen_id="scr_c1")
    async with projector_pool.acquire() as conn:
        with pytest.raises(SemanticError, match="same system"):
            await patch_camera(conn, cid, {"screen_group_id": foreign_gid})
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM events WHERE event_type = 'camera_admin'") == 0   # txn rolled back


async def test_patch_retired_camera_cannot_reactivate(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    cid = await camera(projector_pool, sid, screen_id="scr_c1", status="retired")
    async with projector_pool.acquire() as conn:
        with pytest.raises(TransitionError) as exc:
            await patch_camera(conn, cid, {"status": "active"})
    assert exc.value.current == "retired" and exc.value.allowed == []


async def test_patch_same_status_is_noop_success(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    cid = await camera(projector_pool, sid, screen_id="scr_c1")
    async with projector_pool.acquire() as conn:
        row = await patch_camera(conn, cid, {"status": "active"})   # shipped-contract idempotency
    assert row["status"] == "active"


# --- I-1 orchestrator amendment (audit-noise invariant) -------------------------

async def test_patch_resend_unchanged_field_journals_only_changed(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    cid = await camera(projector_pool, sid, name="Cam1", screen_id="scr_c1")
    async with projector_pool.acquire() as conn:
        row = await patch_camera(conn, cid, {"name": "Cam1", "failover_eligible": True})
    assert row["failover_eligible"] is True
    ev = await projector_pool.fetchrow(
        "SELECT payload FROM events WHERE event_type = 'camera_admin' ORDER BY id DESC LIMIT 1")
    changes = json.loads(ev["payload"])["changes"]
    assert changes == {"failover_eligible": {"from": False, "to": True}}   # name NOT journaled


async def test_patch_zero_effective_changes_skips_event_and_returns_unchanged(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    cid = await camera(projector_pool, sid, name="Cam1", screen_id="scr_c1")
    before_count = await projector_pool.fetchval(
        "SELECT count(*) FROM events WHERE event_type = 'camera_admin'")
    async with projector_pool.acquire() as conn:
        row = await patch_camera(conn, cid, {"name": "Cam1", "status": "active"})   # all resent, unchanged
    assert row["name"] == "Cam1" and row["status"] == "active"
    after_count = await projector_pool.fetchval(
        "SELECT count(*) FROM events WHERE event_type = 'camera_admin'")
    assert after_count == before_count                     # event SKIPPED (I-1)


# --- route mapping (mocked pool; _client pattern from test_camera_admin) -------

from tests.test_camera_admin import _client  # reuse the shipped helper


def test_route_maps_transition_error_to_409(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("src.main.patch_camera",
                        AsyncMock(side_effect=TransitionError("retired", ())))
    with _client(monkeypatch) as client:
        r = client.patch(f"/cameras/{uuid.uuid4()}", json={"status": "active"})
    assert r.status_code == 409
    assert r.json()["detail"] == {"error": "invalid_transition", "from": "retired", "allowed": []}


def test_route_maps_semantic_error_to_422_string(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("src.main.patch_camera",
                        AsyncMock(side_effect=SemanticError("screen_group must belong to the same system")))
    with _client(monkeypatch) as client:
        r = client.patch(f"/cameras/{uuid.uuid4()}", json={"screen_group_id": str(uuid.uuid4())})
    assert r.status_code == 422
    assert r.json()["detail"] == "screen_group must belong to the same system"


def test_route_accepts_name_now(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("src.main.patch_camera", AsyncMock(return_value={"id": "x", "name": "New"}))
    with _client(monkeypatch) as client:
        r = client.patch(f"/cameras/{uuid.uuid4()}", json={"name": "New"})
    assert r.status_code == 200


def test_route_rejects_null_status(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.patch(f"/cameras/{uuid.uuid4()}", json={"status": None})
    assert r.status_code == 422   # delta 4: explicit null on a non-nullable field
