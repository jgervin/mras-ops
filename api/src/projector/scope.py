"""T6 — Scope resolver: raw screen_id string -> scope-uuid bundle.

The projector holds only the raw ``screen_id`` (+ ``screen_kind``) from the event
payload. It resolves that to the uuid keyspace by joining cameras/displays ->
systems (org/location live on ``systems``, not on the device rows). An
unregistered device yields the NULL bundle (God View "unfiltered" == null) and is
recorded in ``unresolved_devices`` (bump on conflict) — it is NEVER an error.

A small in-process TTL cache fronts the DB so the per-event resolve is not N+1.
"""
import time
from dataclasses import dataclass
from typing import Optional
from uuid import UUID


@dataclass(frozen=True)
class Scope:
    camera_id: Optional[UUID]
    display_id: Optional[UUID]
    system_id: Optional[UUID]
    location_id: Optional[UUID]
    organization_id: Optional[UUID]


NULL_SCOPE = Scope(None, None, None, None, None)

# org/location come from systems; the device table supplies its own id + system_id.
_CAMERA_SQL = (
    "SELECT c.id AS camera_id, NULL::uuid AS display_id, c.system_id, "
    "       s.location_id, s.organization_id "
    "FROM cameras c JOIN systems s ON s.id = c.system_id "
    "WHERE c.screen_id = $1 AND c.status <> 'retired'"
)
_DISPLAY_SQL = (
    "SELECT NULL::uuid AS camera_id, d.id AS display_id, d.system_id, "
    "       s.location_id, s.organization_id "
    "FROM displays d JOIN systems s ON s.id = d.system_id "
    "WHERE d.screen_id = $1 AND d.status <> 'retired'"
)
_SQL_BY_KIND = {"camera": _CAMERA_SQL, "display": _DISPLAY_SQL}


class ScopeResolver:
    """Resolve (screen_id, screen_kind) -> Scope, with a TTL cache.

    ``db`` may be an asyncpg Pool or Connection — both expose fetchrow/execute.
    """

    def __init__(self, db, ttl_seconds: float = 60.0, clock=time.monotonic):
        self._db = db
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict = {}  # (kind, screen_id) -> (Scope, expires_at)
        self.unresolved_count = 0

    async def resolve(self, screen_id, screen_kind, event_id=None) -> Scope:
        key = (screen_kind, screen_id)
        now = self._clock()
        cached = self._cache.get(key)
        if cached is not None and cached[1] > now:
            return cached[0]

        scope = await self._fetch(screen_id, screen_kind)
        if scope is None:
            self.unresolved_count += 1
            await self._record_unresolved(screen_id, screen_kind, event_id)
            scope = NULL_SCOPE

        self._cache[key] = (scope, now + self._ttl)
        return scope

    async def _fetch(self, screen_id, screen_kind) -> Optional[Scope]:
        sql = _SQL_BY_KIND.get(screen_kind)
        if sql is None:
            return None
        row = await self._db.fetchrow(sql, screen_id)
        if row is None:
            return None
        return Scope(
            camera_id=row["camera_id"],
            display_id=row["display_id"],
            system_id=row["system_id"],
            location_id=row["location_id"],
            organization_id=row["organization_id"],
        )

    async def _record_unresolved(self, screen_id, screen_kind, event_id) -> None:
        kind = screen_kind if screen_kind in ("camera", "display") else "camera"
        await self._db.execute(
            "INSERT INTO unresolved_devices (screen_id, kind, event_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (screen_id, kind) "
            "DO UPDATE SET last_seen_at = now(), seen_count = unresolved_devices.seen_count + 1, "
            "event_id = EXCLUDED.event_id",
            screen_id,
            kind,
            event_id,
        )
