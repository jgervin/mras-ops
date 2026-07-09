"""POST /displays/adopt (D11, droppable): unresolved row -> real display, one txn."""
import json
import uuid

import pytest

from src.registry.adopt import AdoptBody, adopt_display
from src.registry.writes import SemanticError
from tests.registry_seed import org_loc_sys, screen_group, unresolved

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def test_adopt_creates_display_deletes_row_journals_adopt(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    uid = await unresolved(projector_pool, screen_id="display-9", kind="display", seen_count=3)
    async with projector_pool.acquire() as conn:
        row = await adopt_display(conn, AdoptBody(unresolved_id=uid, system_id=sid, name="Kiosk 9"))
    assert row["screen_id"] == "display-9"                   # pre-filled from the unresolved row
    assert row["status"] == "offline"                        # D7 staging applies to adoption too
    assert row["name"] == "Kiosk 9"
    assert row["device_id"] is not None                      # D8 identity row minted
    assert await projector_pool.fetchval("SELECT count(*) FROM unresolved_devices") == 0
    ev = json.loads(await projector_pool.fetchval(
        "SELECT payload FROM events WHERE event_type = 'registry_admin' ORDER BY id DESC LIMIT 1"))
    assert ev["action"] == "adopt" and ev["object_id"] == str(row["id"])
    assert ev["adopted_from"] == {"unresolved_id": str(uid), "screen_id": "display-9",
                                  "seen_count": 3}


async def test_adopt_into_group_same_system_only(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    _, _, other = await org_loc_sys(projector_pool, sys_name="Sys2")
    foreign = await screen_group(projector_pool, other)
    uid = await unresolved(projector_pool, screen_id="display-9")
    async with projector_pool.acquire() as conn:
        with pytest.raises(SemanticError, match="same system"):
            await adopt_display(conn, AdoptBody(unresolved_id=uid, system_id=sid,
                                                screen_group_id=foreign))
    assert await projector_pool.fetchval("SELECT count(*) FROM unresolved_devices") == 1  # rollback


async def test_adopt_rejects_camera_kind_and_unknown_id(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    uid = await unresolved(projector_pool, screen_id="scr_ghost", kind="camera")
    async with projector_pool.acquire() as conn:
        with pytest.raises(SemanticError, match="not a display"):
            await adopt_display(conn, AdoptBody(unresolved_id=uid, system_id=sid))
        assert await adopt_display(conn, AdoptBody(unresolved_id=uuid.uuid4(),
                                                   system_id=sid)) is None


# --- route ---------------------------------------------------------------------

from tests.test_camera_admin import _client


def test_adopt_route_codes(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("src.main.adopt_display", AsyncMock(return_value=None))
    with _client(monkeypatch) as client:
        r = client.post("/displays/adopt",
                        json={"unresolved_id": str(uuid.uuid4()), "system_id": str(uuid.uuid4())})
        assert r.status_code == 404
        assert r.json()["detail"] == "unresolved device not found"
        assert client.post("/displays/adopt", json={"system_id": str(uuid.uuid4())}).status_code == 422
