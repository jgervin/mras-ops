"""T4 — Cursor repository: read FOR UPDATE + advance, inside a caller's txn.

Real-DB tests (projector_pool fixture applies all migrations incl. 019).
The at-least-once guarantee is that cursor advance and summary upserts share ONE
transaction: the rollback test proves neither persists if the txn aborts.
"""
from datetime import datetime, timezone

import asyncpg
import pytest

from src.projector.cursor import read_cursor_for_update, advance_cursor

_TS = datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc)


async def _reset(pool):
    await pool.execute(
        "UPDATE projector_state SET cursor=0, last_event_ts=NULL, projector_ver=NULL WHERE id=1"
    )


async def test_read_cursor_returns_seed_zero(projector_pool):
    await _reset(projector_pool)
    async with projector_pool.acquire() as conn:
        async with conn.transaction():
            assert await read_cursor_for_update(conn) == 0


async def test_read_for_update_holds_row_lock(projector_pool):
    await _reset(projector_pool)
    async with projector_pool.acquire() as c1, projector_pool.acquire() as c2:
        async with c1.transaction():
            await read_cursor_for_update(c1)  # takes the row lock
            # a second session cannot grab the same row FOR UPDATE NOWAIT
            with pytest.raises(asyncpg.PostgresError):
                await c2.fetchval("SELECT cursor FROM projector_state WHERE id=1 FOR UPDATE NOWAIT")


async def test_advance_updates_cursor_ts_and_ver(projector_pool):
    await _reset(projector_pool)
    ts = _TS
    async with projector_pool.acquire() as conn:
        async with conn.transaction():
            await advance_cursor(conn, 137, ts, "test-ver-1")
    row = await projector_pool.fetchrow(
        "SELECT cursor, last_event_ts, projector_ver FROM projector_state WHERE id=1"
    )
    assert row["cursor"] == 137
    assert row["projector_ver"] == "test-ver-1"
    assert row["last_event_ts"] is not None
    await _reset(projector_pool)


async def test_advance_and_upsert_share_one_txn_rollback(projector_pool):
    await _reset(projector_pool)
    with pytest.raises(RuntimeError):
        async with projector_pool.acquire() as conn:
            async with conn.transaction():
                await advance_cursor(conn, 999, _TS, "should-not-persist")
                await conn.execute(
                    "INSERT INTO unresolved_devices (screen_id, kind) VALUES ($1,$2)",
                    "rollback-screen", "camera",
                )
                raise RuntimeError("force rollback")
    # neither the cursor advance nor the sibling write survived the aborted txn
    assert await projector_pool.fetchval("SELECT cursor FROM projector_state WHERE id=1") == 0
    assert await projector_pool.fetchval(
        "SELECT count(*) FROM unresolved_devices WHERE screen_id='rollback-screen'"
    ) == 0
