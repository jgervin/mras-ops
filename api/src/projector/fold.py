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
from src.projector.handlers import ResolveMiss
from src.projector.routing import route
from src.projector.scope import NULL_SCOPE

_VALID_KINDS = ("camera", "display")

# FIX 4: select strictly in id order (no ts filter). The settle window is applied
# in Python as a STOP boundary so a held-back low-id event is never jumped over.
_SELECT_BATCH = "SELECT * FROM events WHERE id > $1 ORDER BY id ASC LIMIT $2"


async def fold_batch(conn, resolver, cfg) -> dict:
    """Fold one batch. Returns {folded, skipped, cursor, batch}."""
    folded = skipped = 0
    async with conn.transaction():
        cursor = await read_cursor_for_update(conn)
        rows = await conn.fetch(_SELECT_BATCH, cursor, cfg.batch_size)
        if not rows:
            return {"folded": 0, "skipped": 0, "cursor": cursor, "batch": 0}

        # FIX 4: single settle boundary for the whole batch. Any event newer than
        # this is NOT yet settled — stop there and leave it (and every later event)
        # for a future batch, advancing the cursor only to the last settled event.
        boundary = await conn.fetchval(
            "SELECT now() - ($1::bigint * interval '1 millisecond')", cfg.settle_ms
        )

        last_id, last_ts, processed = cursor, None, 0
        for row in rows:
            env = EventEnvelope.from_row(row)
            if env.ts > boundary:
                break  # unsettled — STOP boundary, do not process this or any later event
            try:
                async with conn.transaction():  # per-event savepoint
                    scope = NULL_SCOPE
                    has_scope = env.screen_kind in _VALID_KINDS and env.screen_id is not None
                    if has_scope:
                        scope = await resolver.resolve(env.screen_id, env.screen_kind, env.id)
                    handler = route(env)
                    extra = None
                    if handler is not None:
                        extra = await handler(conn, env, scope)
                        folded += 1
                    # FIX 2: back-stamp AFTER the handler so handler-derived event-scope
                    # columns (subject_profile_id, ad_run_id) — and any FK target the
                    # handler just inserted — are stamped in the SAME savepoint txn.
                    if has_scope:
                        await _backstamp(conn, env.id, scope, extra or {})
            except ResolveMiss as exc:  # FIX 5: required parent row absent (data-completeness)
                await _write_skip(conn, env, exc, cfg.projector_ver, action="projector.resolve_miss")
                skipped += 1
            except Exception as exc:  # noqa: BLE001 — one bad event must not wedge the batch
                await _write_skip(conn, env, exc, cfg.projector_ver)
                skipped += 1
            last_id, last_ts, processed = env.id, env.ts, processed + 1

        if processed:
            await advance_cursor(conn, last_id, last_ts, cfg.projector_ver)
        return {"folded": folded, "skipped": skipped, "cursor": last_id, "batch": len(rows)}


async def _backstamp(conn, event_id, scope, extra=None) -> None:
    """Stamp the resolved scope uuids + handler-derived event-scope columns onto the
    events row (Decision 2 back-stamp). subject_profile_id / ad_run_id are COALESCE'd
    so an event that does not derive them keeps its existing value."""
    extra = extra or {}
    await conn.execute(
        "UPDATE events SET organization_id=$2, location_id=$3, system_id=$4, "
        "camera_id=$5, display_id=$6, "
        "subject_profile_id=COALESCE($7, subject_profile_id), "
        "ad_run_id=COALESCE($8, ad_run_id) WHERE id=$1",
        event_id,
        scope.organization_id,
        scope.location_id,
        scope.system_id,
        scope.camera_id,
        scope.display_id,
        extra.get("subject_profile_id"),
        extra.get("ad_run_id"),
    )


async def _write_skip(conn, env, exc, projector_ver, action="projector.skip") -> None:
    """Record a skipped event in audit_logs — shape only, never the raw payload (PII scrub).

    ``action`` is 'projector.skip' for a bad event (poison/enum/malformed) or
    'projector.resolve_miss' for a missing required parent row (FIX 5)."""
    before = {"service": env.service, "event_type": env.event_type, "status": env.status}
    after = {
        "error_class": type(exc).__name__,
        "error_message": str(exc)[:500],
        "projector_ver": projector_ver,
    }
    await conn.execute(
        "INSERT INTO audit_logs (actor_type, action, entity_type, entity_id, before, after) "
        "VALUES ('system',$4,'event',$1,$2::jsonb,$3::jsonb)",
        str(env.id),
        json.dumps(before),
        json.dumps(after),
        action,
    )
