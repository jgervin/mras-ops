"""POST /cameras + /displays: staged creation (D7), devices identity row (D8),
registry_admin action=create (D10)."""
import json
import uuid

import pytest

import asyncpg

from src.registry.devices import CameraCreate, DisplayCreate, create_camera, create_display
from src.registry.writes import SemanticError
from tests.registry_seed import org_loc_sys, screen_group

pytestmark = pytest.mark.usefixtures("godview_isolate")


async def test_create_camera_is_offline_and_mints_device_row(projector_pool):
    _, loc, sid = await org_loc_sys(projector_pool)
    body = CameraCreate(system_id=sid, name="New Cam", screen_id="scr_new",
                        calibration={"cam_index": 1}, serial_number="SN-1")
    async with projector_pool.acquire() as conn:
        row = await create_camera(conn, body)
    assert row["status"] == "offline"                      # D7: staged, never live at birth
    assert row["camera_role"] == "detection"
    assert row["screen_id"] == "scr_new"
    assert row["location_id"] == loc                       # denormalized from the system
    dev = await projector_pool.fetchrow(
        "SELECT system_id, location_id, device_type::text AS device_type, name, "
        "serial_number, status::text AS status FROM devices WHERE id = $1", row["device_id"])
    assert dev is not None                                 # D8: identity row in the SAME txn
    assert dev["device_type"] == "camera" and dev["system_id"] == sid
    assert dev["name"] == "New Cam" and dev["serial_number"] == "SN-1"
    assert dev["status"] == "offline"
    ev = json.loads(await projector_pool.fetchval(
        "SELECT payload FROM events WHERE event_type = 'registry_admin' ORDER BY id DESC LIMIT 1"))
    assert ev["action"] == "create" and ev["object_id"] == str(row["id"])
    assert ev["changes"]["status"] == {"from": None, "to": "offline"}


async def test_create_camera_without_screen_id_or_name(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    async with projector_pool.acquire() as conn:
        row = await create_camera(conn, CameraCreate(system_id=sid))
    assert row["screen_id"] is None                        # nullable for cameras (012)
    dev_name = await projector_pool.fetchval(
        "SELECT name FROM devices WHERE id = $1", row["device_id"])
    assert dev_name == "camera"                            # devices.name NOT NULL fallback


async def test_create_camera_respects_supplied_device_id(projector_pool):
    _, loc, sid = await org_loc_sys(projector_pool)
    dev = uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO devices (id,system_id,device_type,name) VALUES ($1,$2,'camera','pre')", dev, sid)
    async with projector_pool.acquire() as conn:
        row = await create_camera(conn, CameraCreate(system_id=sid, device_id=dev))
    assert row["device_id"] == dev
    assert await projector_pool.fetchval("SELECT count(*) FROM devices") == 1   # no extra row


async def test_create_rejects_unknown_system_and_cross_system_group(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    _, _, other = await org_loc_sys(projector_pool, sys_name="Sys2")
    foreign = await screen_group(projector_pool, other)
    async with projector_pool.acquire() as conn:
        with pytest.raises(SemanticError, match="unknown system_id"):
            await create_camera(conn, CameraCreate(system_id=uuid.uuid4()))
        with pytest.raises(SemanticError, match="same system"):
            await create_camera(conn, CameraCreate(system_id=sid, screen_group_id=foreign))


async def test_create_display_requires_screen_id_and_dupes_conflict(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    async with projector_pool.acquire() as conn:
        row = await create_display(conn, DisplayCreate(system_id=sid, screen_id="display-7",
                                                       display_role="ambient"))
        assert row["status"] == "offline" and row["display_role"] == "ambient"
        with pytest.raises(asyncpg.UniqueViolationError):   # route maps to 409 (delta 1)
            await create_display(conn, DisplayCreate(system_id=sid, screen_id="display-7"))


# --- routes ----------------------------------------------------------------------

from tests.test_camera_admin import _client


def test_post_routes_status_codes(monkeypatch):
    from unittest.mock import AsyncMock
    created = {"id": str(uuid.uuid4()), "status": "offline"}
    monkeypatch.setattr("src.main.create_camera", AsyncMock(return_value=created))
    with _client(monkeypatch) as client:
        r = client.post("/cameras", json={"system_id": str(uuid.uuid4())})
        assert r.status_code == 201 and r.json()["status"] == "offline"
        # D7: status at birth is not writable — extra="forbid"
        assert client.post("/cameras", json={"system_id": str(uuid.uuid4()),
                                             "status": "active"}).status_code == 422
        # DisplayCreate requires screen_id (NOT NULL, 012)
        assert client.post("/displays", json={"system_id": str(uuid.uuid4())}).status_code == 422
    monkeypatch.setattr("src.main.create_display",
                        AsyncMock(side_effect=asyncpg.UniqueViolationError("dup")))
    with _client(monkeypatch) as client:
        r = client.post("/displays", json={"system_id": str(uuid.uuid4()), "screen_id": "d-1"})
        assert r.status_code == 409
        assert r.json()["detail"] == "screen_id already registered"
