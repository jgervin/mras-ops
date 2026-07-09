"""Migration 028: partial expression indexes for per-object audit trails (spec D10, §6).

Same pattern as 027's events_camera_duty_idx. projector_pool applies every
db/migrations/*.sql in sorted order, so these assert the file exists AND parses."""


async def test_registry_admin_partial_index_exists(projector_pool):
    idx = await projector_pool.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'events_registry_admin_idx'")
    assert idx is not None
    assert "object_id" in idx
    assert "registry_admin" in idx          # partial: WHERE event_type = 'registry_admin'
    assert "DESC" in idx                    # (expr, id DESC) serves ORDER BY id DESC probes


async def test_camera_admin_partial_index_exists(projector_pool):
    idx = await projector_pool.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname = 'events_camera_admin_idx'")
    assert idx is not None
    assert "camera_id" in idx
    assert "camera_admin" in idx
