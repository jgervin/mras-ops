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


def test_upload_component_returns_db_uuid_not_composition_id(monkeypatch):
    # The sidecar returns the COMPOSITION id ("comp-neon"); ops-api must return the DB UUID
    # (what the ads FK + /preview lookup use) so the frontend can preview right after upload.
    sidecar_resp = {"id": "comp-neon", "slug": "neon", "propsSchema": {"x": 1}, "status": "ready"}

    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 200
    mock_http_resp.json.return_value = sidecar_resp

    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=mock_http_resp)
    mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_http_instance.__aexit__ = AsyncMock(return_value=False)

    mock_async_client_cls = MagicMock(return_value=mock_http_instance)

    db_uuid = "11111111-1111-1111-1111-111111111111"
    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock(return_value={"id": db_uuid})

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
    body = resp.json()
    assert body["id"] == db_uuid          # DB UUID, NOT the sidecar's "comp-neon"
    assert body["slug"] == "neon"
    assert body["propsSchema"] == {"x": 1}
    mock_http_instance.post.assert_awaited_once()
    fake_pool.fetchrow.assert_awaited_once()


def test_list_components_decodes_jsonb(monkeypatch):
    fake_pool = AsyncMock()
    fake_pool.fetch = AsyncMock(return_value=[{
        "id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "name": "Neon",
        "slug": "neon",
        "status": "ready",
        "error": None,
        "props_schema": '{"color": "string"}',
        "created_at": "2026-06-08T00:00:00+00:00",
    }])

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setattr("src.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool))

    with TestClient(app) as client:
        resp = client.get("/components")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data[0]["props_schema"], dict)
    assert data[0]["props_schema"] == {"color": "string"}


def test_list_ads_decodes_jsonb(monkeypatch):
    fake_pool = AsyncMock()
    fake_pool.fetch = AsyncMock(return_value=[{
        "id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "name": "Summer Ad",
        "base_video": "summer.mp4",
        "component_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
        "default_props": '{"text": "hello"}',
        "personalized_field": "text",
        "is_active": True,
        "created_at": "2026-06-08T00:00:00+00:00",
    }])

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setattr("src.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool))

    with TestClient(app) as client:
        resp = client.get("/ads")

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data[0]["default_props"], dict)
    assert data[0]["default_props"] == {"text": "hello"}


def test_patch_ad_toggles_is_active(monkeypatch):
    fake_pool = AsyncMock()
    fake_pool.execute = AsyncMock()

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setattr("src.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool))

    ad_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    with TestClient(app) as client:
        # with is_active=True → execute receives True
        resp = client.patch(f"/ads/{ad_id}", json={"is_active": True})
        assert resp.status_code == 200
        call_args = fake_pool.execute.call_args[0]
        assert call_args[1] is True

        fake_pool.execute.reset_mock()

        # missing is_active key → must not crash; execute receives False not None
        resp = client.patch(f"/ads/{ad_id}", json={})
        assert resp.status_code == 200
        call_args = fake_pool.execute.call_args[0]
        assert call_args[1] is False


def test_upload_component_surfaces_sidecar_error(monkeypatch):
    mock_http_resp = MagicMock()
    mock_http_resp.status_code = 422
    mock_http_resp.text = "Unprocessable Entity"

    mock_http_instance = AsyncMock()
    mock_http_instance.post = AsyncMock(return_value=mock_http_resp)
    mock_http_instance.__aenter__ = AsyncMock(return_value=mock_http_instance)
    mock_http_instance.__aexit__ = AsyncMock(return_value=False)

    mock_async_client_cls = MagicMock(return_value=mock_http_instance)

    fake_pool = AsyncMock()
    fake_pool.fetchrow = AsyncMock()

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setattr("src.main.asyncpg.create_pool", AsyncMock(return_value=fake_pool))
    monkeypatch.setattr("src.main.httpx.AsyncClient", mock_async_client_cls)

    with TestClient(app) as client:
        resp = client.post(
            "/components",
            data={"name": "Bad"},
            files={"file": ("bad.tsx", b"invalid", "text/plain")},
        )

    assert resp.status_code == 502
    fake_pool.fetchrow.assert_not_awaited()
