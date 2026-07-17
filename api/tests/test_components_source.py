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


from src.godview.ad_runs import get_ad_run


async def _seed_run_with_component(pool, *, with_ad=True):
    slug = f"comp-{uuid.uuid4()}"
    comp_id = await pool.fetchval(
        "INSERT INTO components (name, slug, source, props_schema) "
        "VALUES ('Comp', $1, $2, '{\"type\": \"object\"}'::jsonb) RETURNING id",
        slug, SOURCE)
    ad_id = None
    if with_ad:
        ad_id = await pool.fetchval(
            "INSERT INTO ads (name, base_video, component_id, default_props, personalized_field) "
            "VALUES ('Ad', 'base.mp4', $1, '{\"text\": \"Hello\"}'::jsonb, 'name') RETURNING id",
            comp_id)
    trig, cr, run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await pool.execute(
        "INSERT INTO composition_runs (id, trigger_id, render_mode, status, ad_id, component_id) "
        "VALUES ($1, $2, 'remotion', 'rendered', $3, $4)", cr, trig, ad_id, comp_id)
    await pool.execute(
        "INSERT INTO ad_runs (id, trigger_id, composition_run_id, status) "
        "VALUES ($1, $2, $3, 'completed')", run, trig, cr)
    return run, slug


async def test_ad_run_detail_carries_component_source(projector_pool):
    run, slug = await _seed_run_with_component(projector_pool)
    d = await get_ad_run(projector_pool, run)
    cr = d["composition_run"]
    assert cr["source"] == SOURCE
    assert cr["component_slug"] == slug
    assert cr["props_schema"] == {"type": "object"}
    assert cr["default_props"] == {"text": "Hello"}
    assert cr["personalized_field"] == "name"
    assert cr["base_video"] == "base.mp4"
    # pre-existing keys still present
    assert cr["render_mode"] == "remotion"
    assert str(cr["component_id"])


async def test_ad_run_detail_null_safe_without_component(projector_pool):
    trig, cr_id, run = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await projector_pool.execute(
        "INSERT INTO composition_runs (id, trigger_id, render_mode, status) "
        "VALUES ($1, $2, 'prebuilt', 'selected')", cr_id, trig)
    await projector_pool.execute(
        "INSERT INTO ad_runs (id, trigger_id, composition_run_id, status) "
        "VALUES ($1, $2, $3, 'completed')", run, trig, cr_id)
    d = await get_ad_run(projector_pool, run)
    cr = d["composition_run"]
    for field in ("source", "component_slug", "props_schema",
                  "default_props", "personalized_field", "base_video"):
        assert cr[field] is None
    assert cr["render_mode"] == "prebuilt"
