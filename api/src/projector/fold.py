"""T9 — Batch fold: the projector's atomic read-route-project-advance cycle.

One transaction on one connection:
  1. read the cursor FOR UPDATE (locks the singleton projector_state row);
  2. select events WHERE id > cursor, oldest-first, up to batch_size, honoring a
     settle window (ignore events newer than now() - settle_ms);
  3. per event: resolve scope once, route to its handler (project the summary
     row), and back-stamp the resolved scope uuids onto the source events row's
     typed scope columns (services leave them NULL — this makes the 017 indexes
     live). Each event runs in its own savepoint;
  4. a per-event handler error is caught, logged to audit_logs
     (action='projector.skip', PII-scrubbed), and the event is skipped — the
     cursor still advances past it so a poison event never wedges the pipeline;
  5. advance the cursor to the last event id in the SAME txn, then commit.

At-least-once holds because the upserts, the back-stamps, and the cursor advance
all commit or roll back together.
"""
import json

from src.projector.cursor import read_cursor_for_update, advance_cursor
from src.projector.events import EventEnvelope
from src.projector.routing import route
from src.projector.scope import NULL_SCOPE

_VALID_KINDS = ("camera", "display")

_SELECT_BATCH = (
    "SELECT * FROM events "
    "WHERE id > $1 AND ts <= now() - ($2::bigint * interval '1 millisecond') "
    "ORDER BY id ASC LIMIT $3"
)


async def fold_batch(conn, resolver, cfg) -> dict:
    """Fold one batch. Returns {folded, skipped, cursor, batch}."""
    folded = skipped = 0
    async with conn.transaction():
        cursor = await read_cursor_for_update(conn)
        rows = await conn.fetch(_SELECT_BATCH, cursor, cfg.settle_ms, cfg.batch_size)
        if not rows:
            return {"folded": 0, "skipped": 0, "cursor": cursor, "batch": 0}

        last_id, last_ts = cursor, None
        for row in rows:
            env = EventEnvelope.from_row(row)
            try:
                async with conn.transaction():  # per-event savepoint
                    scope = NULL_SCOPE
                    if env.screen_kind in _VALID_KINDS and env.screen_id is not None:
                        scope = await resolver.resolve(env.screen_id, env.screen_kind, env.id)
                        await _backstamp(conn, env.id, scope)
                    handler = route(env)
                    if handler is not None:
                        await handler(conn, env, scope)
                        folded += 1
            except Exception as exc:  # noqa: BLE001 — one bad event must not wedge the batch
                await _write_skip(conn, env, exc, cfg.projector_ver)
                skipped += 1
            last_id, last_ts = env.id, env.ts

        await advance_cursor(conn, last_id, last_ts, cfg.projector_ver)
        return {"folded": folded, "skipped": skipped, "cursor": last_id, "batch": len(rows)}


async def _backstamp(conn, event_id, scope) -> None:
    """Stamp the resolved scope uuids onto the events row (Decision 2 back-stamp)."""
    await conn.execute(
        "UPDATE events SET organization_id=$2, location_id=$3, system_id=$4, "
        "camera_id=$5, display_id=$6 WHERE id=$1",
        event_id,
        scope.organization_id,
        scope.location_id,
        scope.system_id,
        scope.camera_id,
        scope.display_id,
    )


async def _write_skip(conn, env, exc, projector_ver) -> None:
    """Record a poison event in audit_logs — shape only, never the raw payload (PII scrub)."""
    before = {"service": env.service, "event_type": env.event_type, "status": env.status}
    after = {
        "error_class": type(exc).__name__,
        "error_message": str(exc)[:500],
        "projector_ver": projector_ver,
    }
    await conn.execute(
        "INSERT INTO audit_logs (actor_type, action, entity_type, entity_id, before, after) "
        "VALUES ('system','projector.skip','event',$1,$2::jsonb,$3::jsonb)",
        str(env.id),
        json.dumps(before),
        json.dumps(after),
    )
