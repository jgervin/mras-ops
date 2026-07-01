"""T6 — Scope resolver: (screen_id, screen_kind) -> scope-uuid bundle.

Real DB. Resolver joins cameras/displays -> systems for org/location (there is no
org column on cameras/displays). Unknown screen_id -> null bundle + an upsert into
unresolved_devices (bump on conflict), never raises. TTL cache in front.
"""
import pytest

from src.projector.scope import ScopeResolver, NULL_SCOPE


async def _seed(pool):
    """Seed one org/location/system + a camera (screen_0) and display (display-1).

    Idempotent: the module-scoped DB is shared and screen_id is globally UNIQUE
    (migration 020), so re-seeding returns the existing bundle instead of colliding.
    """
    existing = await pool.fetchrow(
        "SELECT c.id AS cam, c.system_id AS sys, s.organization_id AS org, s.location_id AS loc "
        "FROM cameras c JOIN systems s ON s.id = c.system_id WHERE c.screen_id = 'screen_0'"
    )
    if existing:
        disp = await pool.fetchval("SELECT id FROM displays WHERE screen_id='display-1'")
        return {"org": existing["org"], "loc": existing["loc"], "sys": existing["sys"],
                "cam": existing["cam"], "disp": disp}
    org = await pool.fetchval(
        "INSERT INTO organizations (name, organization_type) VALUES ('Acme','host') RETURNING id"
    )
    loc = await pool.fetchval(
        "INSERT INTO locations (name, location_type) VALUES ('Store 1','store') RETURNING id"
    )
    sys = await pool.fetchval(
        "INSERT INTO systems (organization_id, location_id, name) VALUES ($1,$2,'Sys 1') RETURNING id",
        org, loc,
    )
    cam = await pool.fetchval(
        "INSERT INTO cameras (system_id, screen_id, name) VALUES ($1,'screen_0','Cam') RETURNING id", sys
    )
    disp = await pool.fetchval(
        "INSERT INTO displays (system_id, screen_id, name) VALUES ($1,'display-1','Disp') RETURNING id", sys
    )
    return {"org": org, "loc": loc, "sys": sys, "cam": cam, "disp": disp}


class _RaisingDB:
    async def fetchrow(self, *a, **k):
        raise AssertionError("DB hit on a cache hit")

    async def execute(self, *a, **k):
        raise AssertionError("DB hit on a cache hit")


async def test_known_camera_resolves_full_bundle(projector_pool):
    ids = await _seed(projector_pool)
    r = ScopeResolver(projector_pool)
    scope = await r.resolve("screen_0", "camera")
    assert scope.camera_id == ids["cam"]
    assert scope.display_id is None
    assert scope.system_id == ids["sys"]
    assert scope.location_id == ids["loc"]
    assert scope.organization_id == ids["org"]


async def test_known_display_resolves_display_bundle(projector_pool):
    ids = await _seed(projector_pool)
    r = ScopeResolver(projector_pool)
    scope = await r.resolve("display-1", "display")
    assert scope.display_id == ids["disp"]
    assert scope.camera_id is None
    assert scope.system_id == ids["sys"]
    assert scope.organization_id == ids["org"]


async def test_unknown_screen_returns_null_bundle_and_records_once(projector_pool):
    await _seed(projector_pool)
    r = ScopeResolver(projector_pool)
    scope = await r.resolve("screen_9", "camera")
    assert scope == NULL_SCOPE
    assert r.unresolved_count == 1
    # second call (within TTL) is a cache hit: no new counter, no duplicate row
    scope2 = await r.resolve("screen_9", "camera")
    assert scope2 == NULL_SCOPE
    assert r.unresolved_count == 1
    rows = await projector_pool.fetch(
        "SELECT screen_id, kind, seen_count FROM unresolved_devices WHERE screen_id='screen_9'"
    )
    assert len(rows) == 1
    assert rows[0]["kind"] == "camera"
    assert rows[0]["seen_count"] == 1


async def test_unresolved_upsert_bumps_seen_count_on_repeat_db_visit(projector_pool):
    await _seed(projector_pool)
    # ttl=0 forces a DB visit every call, exercising the ON CONFLICT bump path.
    r = ScopeResolver(projector_pool, ttl_seconds=0)
    await r.resolve("screen_bump", "camera")
    await r.resolve("screen_bump", "camera")
    row = await projector_pool.fetchrow(
        "SELECT seen_count FROM unresolved_devices WHERE screen_id='screen_bump'"
    )
    assert row["seen_count"] == 2


async def test_cache_hit_does_not_touch_db(projector_pool):
    await _seed(projector_pool)
    r = ScopeResolver(projector_pool)
    first = await r.resolve("screen_0", "camera")
    r._db = _RaisingDB()  # any DB access now blows up
    second = await r.resolve("screen_0", "camera")
    assert second == first  # served from cache, no DB hit


async def test_resolve_never_raises_on_unknown(projector_pool):
    await _seed(projector_pool)
    r = ScopeResolver(projector_pool)
    # must not raise even though the device is unregistered
    scope = await r.resolve("totally-unknown", "display")
    assert scope == NULL_SCOPE


async def test_unresolved_conflict_refreshes_event_id(projector_pool):
    """ON CONFLICT must update event_id to the most-recent unresolved event.

    Insert two real events (to satisfy the FK), then call resolve twice with
    ttl=0 so each call hits the DB.  After the second call: seen_count==2 and
    event_id must equal the second (newer) event's id.
    """
    await _seed(projector_pool)
    eid_a = await projector_pool.fetchval(
        "INSERT INTO events (trigger_id, service, event_type, status) "
        "VALUES (gen_random_uuid(),'test','test','test') RETURNING id"
    )
    eid_b = await projector_pool.fetchval(
        "INSERT INTO events (trigger_id, service, event_type, status) "
        "VALUES (gen_random_uuid(),'test','test','test') RETURNING id"
    )
    r = ScopeResolver(projector_pool, ttl_seconds=0)
    await r.resolve("screen_evtid_refresh", "camera", event_id=eid_a)
    await r.resolve("screen_evtid_refresh", "camera", event_id=eid_b)
    row = await projector_pool.fetchrow(
        "SELECT event_id, seen_count FROM unresolved_devices WHERE screen_id='screen_evtid_refresh'"
    )
    assert row["seen_count"] == 2
    assert row["event_id"] == eid_b  # must be refreshed to the most-recent event
