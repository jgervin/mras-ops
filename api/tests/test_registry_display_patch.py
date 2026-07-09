"""PATCH /displays/{id} (fleet P2): first display write ever; journals
registry_admin. Includes the I-1 orchestrator amendment (2026-07-08, BINDING):
journaled changes filtered to from != to; zero-effective-change PATCH skips
the event."""
import json
import uuid

import pytest

from src.registry.devices import patch_display
from src.registry.lifecycle import TransitionError
from src.registry.writes import SemanticError
from tests.registry_seed import display, org_loc_sys, screen_group

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def test_patch_updates_and_journals_registry_admin_update(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    did = await display(projector_pool, sid, name="Kiosk 1", screen_id="display-1")
    async with projector_pool.acquire() as conn:
        row = await patch_display(conn, did, {"name": "Lobby Kiosk", "display_role": "ambient",
                                              "resolution_width": 1920, "resolution_height": 1080})
    assert row["name"] == "Lobby Kiosk"
    assert row["display_role"] == "ambient"
    assert row["resolution_width"] == 1920
    assert row["screen_id"] == "display-1"                       # identity untouched
    ev = await projector_pool.fetchrow(
        "SELECT system_id, display_id, payload FROM events "
        "WHERE event_type = 'registry_admin' ORDER BY id DESC LIMIT 1")
    assert ev["system_id"] == sid and ev["display_id"] == did
    payload = json.loads(ev["payload"])
    assert payload["object_type"] == "display" and payload["object_id"] == str(did)
    assert payload["action"] == "update"
    assert payload["changes"]["display_role"] == {"from": "primary_ad", "to": "ambient"}


async def test_status_only_patch_journals_action_lifecycle(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    did = await display(projector_pool, sid, screen_id="display-1")
    async with projector_pool.acquire() as conn:
        row = await patch_display(conn, did, {"status": "offline"})
    assert row["status"] == "offline"
    payload = json.loads(await projector_pool.fetchval(
        "SELECT payload FROM events WHERE event_type = 'registry_admin' ORDER BY id DESC LIMIT 1"))
    assert payload["action"] == "lifecycle"


async def test_retired_display_is_terminal(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    did = await display(projector_pool, sid, screen_id="display-1", status="retired")
    async with projector_pool.acquire() as conn:
        with pytest.raises(TransitionError):
            await patch_display(conn, did, {"status": "active"})


async def test_cross_system_group_and_ungroup(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    _, _, other = await org_loc_sys(projector_pool, sys_name="Sys2")
    gid = await screen_group(projector_pool, sid)
    foreign = await screen_group(projector_pool, other)
    did = await display(projector_pool, sid, screen_id="display-1", group=gid)
    async with projector_pool.acquire() as conn:
        with pytest.raises(SemanticError, match="same system"):
            await patch_display(conn, did, {"screen_group_id": foreign})
        row = await patch_display(conn, did, {"screen_group_id": None})   # ungroup
    assert row["screen_group_id"] is None


async def test_unknown_display_returns_none(projector_pool):
    async with projector_pool.acquire() as conn:
        assert await patch_display(conn, uuid.uuid4(), {"name": "x"}) is None


# --- I-1 orchestrator amendment (audit-noise invariant) -------------------------

async def test_patch_resend_unchanged_field_journals_only_changed(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    did = await display(projector_pool, sid, name="Kiosk 1", screen_id="display-1")
    async with projector_pool.acquire() as conn:
        row = await patch_display(conn, did, {"name": "Kiosk 1", "resolution_width": 1920})
    assert row["resolution_width"] == 1920
    payload = json.loads(await projector_pool.fetchval(
        "SELECT payload FROM events WHERE event_type = 'registry_admin' ORDER BY id DESC LIMIT 1"))
    assert payload["changes"] == {"resolution_width": {"from": None, "to": 1920}}   # name NOT journaled


async def test_patch_zero_effective_changes_skips_event(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    did = await display(projector_pool, sid, name="Kiosk 1", screen_id="display-1")
    before_count = await projector_pool.fetchval(
        "SELECT count(*) FROM events WHERE event_type = 'registry_admin'")
    async with projector_pool.acquire() as conn:
        row = await patch_display(conn, did, {"name": "Kiosk 1", "status": "active"})  # resent, unchanged
    assert row["name"] == "Kiosk 1" and row["status"] == "active"
    after_count = await projector_pool.fetchval(
        "SELECT count(*) FROM events WHERE event_type = 'registry_admin'")
    assert after_count == before_count                      # event SKIPPED (I-1)


# --- route ---------------------------------------------------------------------

from tests.test_camera_admin import _client


def test_route_validates_and_maps_errors(monkeypatch):
    from unittest.mock import AsyncMock
    with _client(monkeypatch) as client:
        assert client.patch("/displays/not-a-uuid", json={"name": "x"}).status_code == 400
        assert client.patch(f"/displays/{uuid.uuid4()}", json={}).status_code == 400
        assert client.patch(f"/displays/{uuid.uuid4()}", json={"screen_id": "evil"}).status_code == 422
        assert client.patch(f"/displays/{uuid.uuid4()}", json={"display_role": "boss"}).status_code == 422
    monkeypatch.setattr("src.main.patch_display", AsyncMock(return_value=None))
    with _client(monkeypatch) as client:
        assert client.patch(f"/displays/{uuid.uuid4()}", json={"name": "x"}).status_code == 404
