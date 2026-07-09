"""TODO-8 Phase C (ops side): migration 027 + audited PATCH /cameras/{id}."""
import uuid

import pytest

from src.cameras import patch_camera  # add to imports at top

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


# --- patch_camera ------------------------------------------------------------

async def test_patch_updates_fields_and_returns_row(projector_pool):
    _, _, sid = await _org_loc_sys(projector_pool)
    cid = await _camera(projector_pool, sid)
    async with projector_pool.acquire() as conn:
        row = await patch_camera(conn, cid, {"camera_role": "standby", "failover_eligible": True})
    assert row["camera_role"] == "standby"
    assert row["failover_eligible"] is True
    assert row["status"] == "active"          # untouched field preserved
    assert row["name"] == "Cam1"              # identity never changes (spec §2)


async def test_patch_status_only(projector_pool):
    _, _, sid = await _org_loc_sys(projector_pool)
    cid = await _camera(projector_pool, sid)
    async with projector_pool.acquire() as conn:
        row = await patch_camera(conn, cid, {"status": "offline"})
    assert row["status"] == "offline"
    assert row["camera_role"] == "detection"  # role untouched (decision 12: offline != demote)


async def test_patch_journals_camera_admin_event(projector_pool):
    _, _, sid = await _org_loc_sys(projector_pool)
    cid = await _camera(projector_pool, sid)
    async with projector_pool.acquire() as conn:
        await patch_camera(conn, cid, {"camera_role": "standby"})
    ev = await projector_pool.fetchrow(
        "SELECT service, status, system_id, camera_id, payload FROM events "
        "WHERE event_type = 'camera_admin' ORDER BY id DESC LIMIT 1")
    assert ev is not None and ev["service"] == "mras-ops" and ev["status"] == "success"
    assert ev["camera_id"] == cid and ev["system_id"] == sid
    import json
    payload = json.loads(ev["payload"])
    assert payload["changes"]["camera_role"] == {"from": "detection", "to": "standby"}


async def test_patch_unknown_camera_returns_none_and_journals_nothing(projector_pool):
    async with projector_pool.acquire() as conn:
        assert await patch_camera(conn, uuid.uuid4(), {"camera_role": "standby"}) is None
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM events WHERE event_type = 'camera_admin'") == 0


# --- route validation (TestClient + mocked pool, per test_registry.py) -------

class _AcquireCtx:
    """route uses `async with _db.acquire() as conn:` — AsyncMock().acquire() returns
    an un-awaited coroutine, not an async context manager, so it needs a real one.
    (Deviation from plan's literal _client(); test_registry.py's routes never call
    _db.acquire(), so there was no existing precedent for this pattern.)"""
    async def __aenter__(self):
        from unittest.mock import AsyncMock
        return AsyncMock()

    async def __aexit__(self, *exc):
        return False


def _client(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from fastapi.testclient import TestClient
    from src.main import app
    fake_pool = AsyncMock()  # supports `await _db.close()` on lifespan shutdown
    fake_pool.acquire = MagicMock(return_value=_AcquireCtx())  # NOT an AsyncMock: acquire()
    # must return the context manager synchronously, to be used with `async with`.
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setattr("src.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool))
    return TestClient(app)


def test_route_rejects_unknown_field(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.patch(f"/cameras/{uuid.uuid4()}", json={"screen_id": "evil"})
    assert r.status_code == 422  # extra="forbid": identity is not writable (spec D2 — name is CONFIG now)


def test_route_rejects_bad_enum_value(monkeypatch):
    with _client(monkeypatch) as client:
        r = client.patch(f"/cameras/{uuid.uuid4()}", json={"camera_role": "boss"})
    assert r.status_code == 422


def test_route_rejects_bad_uuid_and_empty_patch(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.patch("/cameras/not-a-uuid", json={"status": "offline"}).status_code == 400
        assert client.patch(f"/cameras/{uuid.uuid4()}", json={}).status_code == 400


def test_route_404_on_unknown_camera(monkeypatch):
    from unittest.mock import AsyncMock
    monkeypatch.setattr("src.main.patch_camera", AsyncMock(return_value=None))
    with _client(monkeypatch) as client:
        r = client.patch(f"/cameras/{uuid.uuid4()}", json={"camera_role": "standby"})
    assert r.status_code == 404
