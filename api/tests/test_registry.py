"""
M4 Task 2 – component upload + ad CRUD registry tests.
Red phase: routes do not exist yet → all tests fail (404 / AttributeError).
"""
import os
from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

from src.main import app


def test_create_ad_persists_and_returns_id(monkeypatch):
    fake_id = "11111111-1111-1111-1111-111111111111"
    fake_row = {
        "id": fake_id,
        "name": "Test Ad",
        "base_video": "standard.mp4",
        "component_id": "22222222-2222-2222-2222-222222222222",
        "default_props": "{}",
        "personalized_field": "text",
        "is_active": False,
        "created_at": "2026-06-08T00:00:00+00:00",
    }
    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(return_value=fake_row)

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setattr("src.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool))

    with TestClient(app) as client:
        resp = client.post(
            "/ads",
            json={
                "name": "Test Ad",
                "base_video": "standard.mp4",
                "component_id": "22222222-2222-2222-2222-222222222222",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["id"] == fake_id


def test_upload_component_forwards_to_sidecar_and_persists(monkeypatch):
    sidecar_resp = {"id": "comp-neon", "slug": "neon", "propsSchema": {}, "status": "ready"}

    mock_http_resp = MagicMock()
    mock_http_resp.json.return_value = sidecar_resp

    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=mock_http_resp)
    mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_http_instance.__aexit__ = AsyncMock(return_value=False)

    mock_async_client_cls = MagicMock(return_value=mock_http_instance)

    fake_pool = AsyncMock()
    fake_pool.execute = AsyncMock()

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setattr("src.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool))
    monkeypatch.setattr("src.main.httpx.AsyncClient", mock_async_client_cls)

    with TestClient(app) as client:
        resp = client.post(
            "/components",
            data={"name": "Neon"},
            files={"file": ("neon.tsx", b"export default () => <div/>", "text/plain")},
        )

    assert resp.status_code == 200
    assert resp.json()["slug"] == "neon"
    mock_http_instance.post.assert_awaited_once()
    fake_pool.execute.assert_awaited_once()
