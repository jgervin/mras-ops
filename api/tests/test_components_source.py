"""Component source persistence: migration 029 + ingest storage + god-view exposure.

Spec: /Users/jn/code/minority_report_architecture/docs/superpowers/specs/2026-07-17-remotion-source-node.md
"""
import uuid

import pytest

pytestmark = pytest.mark.usefixtures("godview_isolate")

SOURCE = 'export const Hello = ({name}) => <div className="hi">{name}</div>;'


async def test_components_source_column_persists(projector_pool):
    slug = f"comp-{uuid.uuid4()}"
    await projector_pool.execute(
        "INSERT INTO components (name, slug, source) VALUES ('C', $1, $2)", slug, SOURCE)
    assert await projector_pool.fetchval(
        "SELECT source FROM components WHERE slug = $1", slug) == SOURCE


import io

from starlette.datastructures import UploadFile

import src.main as main


class _FakeResp:
    status_code = 200

    def __init__(self, slug):
        self._slug = slug

    def json(self):
        return {"slug": self._slug, "status": "ready", "error": None,
                "propsSchema": {"type": "object"}}


class _FakeSidecar:
    """Stands in for httpx.AsyncClient so no overlay sidecar is needed."""

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        return _FakeResp("comp-" + json["name"].lower())


async def test_upload_component_persists_source(projector_pool, monkeypatch):
    monkeypatch.setattr(main, "_db", projector_pool)
    monkeypatch.setattr(main.httpx, "AsyncClient", _FakeSidecar)
    name = f"hello{uuid.uuid4().hex[:8]}"
    upload = UploadFile(io.BytesIO(SOURCE.encode()), filename="Hello.tsx")
    resp = await main.upload_component(name=name, file=upload)
    assert await projector_pool.fetchval(
        "SELECT source FROM components WHERE slug = $1", resp["slug"]) == SOURCE
