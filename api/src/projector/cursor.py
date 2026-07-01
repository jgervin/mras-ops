"""T4 — Cursor repository for the singleton ``projector_state`` row (id=1).

Both functions take a connection ALREADY bound to the caller's transaction; they
never open their own. The worker (later task) reads the cursor FOR UPDATE, folds
the batch, and advances the cursor — all in one txn — so the at-least-once
guarantee holds: if anything aborts, the cursor does not move.
"""


async def read_cursor_for_update(conn) -> int:
    """Return the current cursor, locking the singleton row for the txn."""
    return await conn.fetchval("SELECT cursor FROM projector_state WHERE id = 1 FOR UPDATE")


async def advance_cursor(conn, new_cursor: int, last_event_ts, projector_ver: str) -> None:
    """Advance the cursor + heartbeat within the caller's transaction."""
    await conn.execute(
        "UPDATE projector_state "
        "SET cursor = $1, last_event_ts = $2, updated_at = now(), projector_ver = $3 "
        "WHERE id = 1 AND cursor < $1",
        new_cursor,
        last_event_ts,
        projector_ver,
    )
