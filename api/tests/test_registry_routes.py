"""Route-level validation for the fleet registry endpoints (mocked pool;
_AcquireCtx/_client pattern from test_camera_admin.py)."""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


class _AcquireCtx:
    async def __aenter__(self):
        return AsyncMock()

    async def __aexit__(self, *exc):
        return False


def _client(monkeypatch):
    from fastapi.testclient import TestClient
    from src.main import app
    fake_pool = AsyncMock()
    fake_pool.acquire = MagicMock(return_value=_AcquireCtx())
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setattr("src.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool))
    return TestClient(app)


def test_locations_rejects_bad_parent_uuid(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/locations?parent_location_id=nope").status_code == 400


def test_locations_default_root_calls_reader(monkeypatch):
    reader = AsyncMock(return_value={"counts": {"total": 0}, "items": [], "next_cursor": None})
    monkeypatch.setattr("src.main.list_locations", reader)
    with _client(monkeypatch) as client:
        r = client.get("/locations")
    assert r.status_code == 200 and r.json()["counts"] == {"total": 0}
    assert reader.call_args.kwargs["parent_id"] is None            # "root"


def test_systems_requires_location_id(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/systems").status_code == 422           # FastAPI required query param


def test_cameras_requires_exactly_one_scope(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/cameras").status_code == 422
        two = client.get(f"/cameras?system_id={uuid.uuid4()}&screen_group_id={uuid.uuid4()}")
        assert two.status_code == 422
        assert two.json()["detail"] == "provide exactly one of system_id or screen_group_id"


def test_limit_is_clamped(monkeypatch):
    reader = AsyncMock(return_value={"counts": {"total": 0}, "items": [], "next_cursor": None})
    monkeypatch.setattr("src.main.list_organizations", reader)
    with _client(monkeypatch) as client:
        client.get("/organizations?limit=999")
    assert reader.call_args.kwargs["limit"] == 100


def test_detail_404_and_400(monkeypatch):
    monkeypatch.setattr("src.main.get_detail", AsyncMock(return_value=None))
    with _client(monkeypatch) as client:
        assert client.get(f"/screen-groups/{uuid.uuid4()}").status_code == 404
        assert client.get("/cameras/not-a-uuid").status_code == 400


def test_detail_passes_object_type(monkeypatch):
    detail = {"object_type": "display", "identity": {}, "config": {}, "state": {}}
    getter = AsyncMock(return_value=detail)
    monkeypatch.setattr("src.main.get_detail", getter)
    with _client(monkeypatch) as client:
        r = client.get(f"/displays/{uuid.uuid4()}")
    assert r.status_code == 200 and r.json() == detail
    assert getter.call_args.args[1] == "display"


def test_audit_requires_object_id(monkeypatch):
    with _client(monkeypatch) as client:
        assert client.get("/registry/audit").status_code == 422
