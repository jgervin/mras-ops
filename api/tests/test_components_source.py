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
