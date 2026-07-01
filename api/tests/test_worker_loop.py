"""T10 — Worker loop lifecycle, against the throwaway DB + synthetic events.

Proves the three load-bearing guarantees:
  (a) single-writer: a second worker cannot acquire the lock while the first
      holds it, and it does NOT fold while it cannot acquire (passive retry);
  (b) drain: the loop folds a backlog across multiple batches, then idles;
  (c) shutdown: request_stop() releases the advisory lock so a subsequent
      acquire (on any connection) succeeds; asyncio-cancel does the same.

The lock lives on a DEDICATED standalone connection (dedicated_conn_factory) —
never a pooled connection, which would free the session lock on release.
"""
import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone

from src.projector.config import ProjectorConfig
from src.projector.lock import try_acquire, release
from src.projector.worker import ProjectorWorker

# Fast poll so the passive-acquire retry spins several times inside a test sleep.
_ENV = {"PROJECTOR_SETTLE_MS": "0", "PROJECTOR_BATCH_SIZE": "2", "PROJECTOR_POLL_MS": "20"}


async def _fence(pool) -> int:
    fence = await pool.fetchval("SELECT COALESCE(max(id), 0) FROM events")
    await pool.execute("UPDATE projector_state SET cursor=$1 WHERE id=1", fence)
    return fence


async def _seed_display(pool):
    existing = await pool.fetchval("SELECT id FROM displays WHERE screen_id='display-w'")
    if existing:
        return existing
    org = await pool.fetchval(
        "INSERT INTO organizations (name, organization_type) VALUES ('WorkerOrg','host') RETURNING id"
    )
    loc = await pool.fetchval(
        "INSERT INTO locations (name, location_type) VALUES ('WLoc','store') RETURNING id"
    )
    sys = await pool.fetchval(
        "INSERT INTO systems (organization_id, location_id, name) VALUES ($1,$2,'WSys') RETURNING id",
        org, loc,
    )
    return await pool.fetchval(
        "INSERT INTO displays (system_id, screen_id, name) VALUES ($1,'display-w','WDisp') RETURNING id", sys
    )


async def _ins_ad_run(pool):
    """One mapped, always-folds event (ad_run/planned -> ad_runs), unique trigger."""
    ts = datetime.now(timezone.utc) - timedelta(seconds=30)
    payload = {"screen_id": "display-w", "screen_kind": "display", "personalization_type": "none"}
    return await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
        "VALUES ($1,$2,'mras-composer','ad_run','planned',$3::jsonb) RETURNING id",
        str(uuid.uuid4()), ts, json.dumps(payload),
    )


# --------------------------------------------------------------------------- #
# (a) single-writer — second worker cannot acquire and does not fold
# --------------------------------------------------------------------------- #
async def test_second_worker_cannot_acquire_and_does_not_fold(projector_pool, dedicated_conn_factory):
    cfg = ProjectorConfig.from_env(_ENV)
    await _seed_display(projector_pool)
    conn_a = await dedicated_conn_factory()
    conn_b = await dedicated_conn_factory()
    worker_a = ProjectorWorker(projector_pool, conn_a, cfg)
    worker_b = ProjectorWorker(projector_pool, conn_b, cfg)

    assert await worker_a.acquire_lock() is True
    try:
        await _fence(projector_pool)
        await _ins_ad_run(projector_pool)
        runs_before = await projector_pool.fetchval("SELECT count(*) FROM ad_runs")

        # B cannot acquire while A holds it.
        assert await worker_b.acquire_lock() is False
        assert worker_b.holds_lock is False

        # B.run() must stay passive (retry acquisition), never fold, while A holds the lock.
        task = asyncio.create_task(worker_b.run())
        await asyncio.sleep(0.15)  # several poll intervals: B keeps failing to acquire
        assert worker_b.holds_lock is False
        assert await projector_pool.fetchval("SELECT count(*) FROM ad_runs") == runs_before
        worker_b.request_stop()
        await asyncio.wait_for(task, timeout=2)
        assert worker_b.holds_lock is False
    finally:
        await worker_a.release_lock()


# --------------------------------------------------------------------------- #
# (b) drain a backlog across batches, then idle
# --------------------------------------------------------------------------- #
async def test_drain_processes_backlog_then_idles(projector_pool, dedicated_conn_factory):
    cfg = ProjectorConfig.from_env(_ENV)  # batch_size=2
    await _seed_display(projector_pool)
    conn = await dedicated_conn_factory()
    worker = ProjectorWorker(projector_pool, conn, cfg)
    assert await worker.acquire_lock() is True
    try:
        fence = await _fence(projector_pool)
        ids = [await _ins_ad_run(projector_pool) for _ in range(5)]
        runs_before = await projector_pool.fetchval("SELECT count(*) FROM ad_runs")

        # a single batch is capped at batch_size (2) — proves batching.
        r1 = await worker.fold_once()
        assert r1["folded"] == 2

        # drain the remaining 3 across further batches, then stop looping (idle).
        await worker.drain()
        cursor = await projector_pool.fetchval("SELECT cursor FROM projector_state WHERE id=1")
        assert cursor == max(ids)
        assert await projector_pool.fetchval("SELECT count(*) FROM ad_runs") == runs_before + 5

        # caught up: the next fold does nothing.
        r_idle = await worker.fold_once()
        assert r_idle["batch"] == 0
        assert r_idle["folded"] + r_idle["skipped"] == 0
        assert fence < cursor
    finally:
        await worker.release_lock()


# --------------------------------------------------------------------------- #
# (c) shutdown releases the lock (request_stop and cancel)
# --------------------------------------------------------------------------- #
async def test_request_stop_releases_lock(projector_pool, dedicated_conn_factory):
    cfg = ProjectorConfig.from_env(_ENV)
    conn = await dedicated_conn_factory()
    other = await dedicated_conn_factory()
    worker = ProjectorWorker(projector_pool, conn, cfg)

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)  # let it acquire + enter the loop
    assert worker.holds_lock is True
    # while running, another connection cannot take the lock.
    assert await try_acquire(other, cfg.advisory_lock_key) is False

    worker.request_stop()
    await asyncio.wait_for(task, timeout=2)
    assert worker.holds_lock is False
    # lock released -> another connection can now acquire it.
    assert await try_acquire(other, cfg.advisory_lock_key) is True
    await release(other, cfg.advisory_lock_key)


async def test_cancel_releases_lock(projector_pool, dedicated_conn_factory):
    cfg = ProjectorConfig.from_env(_ENV)
    conn = await dedicated_conn_factory()
    other = await dedicated_conn_factory()
    worker = ProjectorWorker(projector_pool, conn, cfg)

    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.1)
    assert worker.holds_lock is True

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert worker.holds_lock is False
    assert await try_acquire(other, cfg.advisory_lock_key) is True
    await release(other, cfg.advisory_lock_key)


# --------------------------------------------------------------------------- #
# Fix 1 — drain "caught-up" signal must count rows CONSUMED (processed),
#          not just folded+skipped.
# --------------------------------------------------------------------------- #
async def _ins_unmapped(pool):
    """Insert an unmapped event (route() returns None → increments processed only)."""
    ts = datetime.now(timezone.utc) - timedelta(seconds=30)
    return await pool.fetchval(
        "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
        "VALUES ($1,$2,'mras-vision','gaze','success','{}') RETURNING id",
        str(uuid.uuid4()), ts,
    )


async def test_drain_continues_through_unmapped_heavy_pages(projector_pool, dedicated_conn_factory):
    """Fix 1: drain must use res["processed"] (not folded+skipped) for caught-up signal.

    A batch of mostly-unmapped events has folded+skipped < batch_size even when
    processed == batch_size (a full page was consumed).  The old check exits early;
    the fixed check keeps draining until a partial page is seen.
    """
    cfg = ProjectorConfig.from_env(_ENV)  # batch_size=2
    await _seed_display(projector_pool)
    conn = await dedicated_conn_factory()
    worker = ProjectorWorker(projector_pool, conn, cfg)
    assert await worker.acquire_lock() is True
    try:
        await _fence(projector_pool)
        # 3 unmapped + 1 mapped = 4 events → two full batches of 2.
        # OLD: batch 1: folded+skipped=0 < batch_size=2 → drain exits at cursor=fence+2.
        # NEW: batch 1: processed=2 == batch_size=2 → keep draining; processes all 4.
        u1 = await _ins_unmapped(projector_pool)
        u2 = await _ins_unmapped(projector_pool)
        u3 = await _ins_unmapped(projector_pool)
        m1 = await _ins_ad_run(projector_pool)
        expected_cursor = max(u1, u2, u3, m1)

        await worker.drain()

        cursor = await projector_pool.fetchval("SELECT cursor FROM projector_state WHERE id=1")
        assert cursor == expected_cursor, (
            f"drain stopped early at cursor={cursor}, expected {expected_cursor} "
            "(all 4 events consumed); old folded+skipped check exits after the first "
            "unmapped-only batch"
        )
    finally:
        await worker.release_lock()


async def test_drain_returns_on_empty_batch_no_busyspin(projector_pool, dedicated_conn_factory):
    """Fix 1: fold_batch must expose 'processed' in its return dict.

    Verifies the return dict has 'processed'==0 for an empty batch (no events),
    and that drain() returns rather than spinning — guards the idle edge case.
    """
    cfg = ProjectorConfig.from_env(_ENV)
    conn = await dedicated_conn_factory()
    worker = ProjectorWorker(projector_pool, conn, cfg)
    assert await worker.acquire_lock() is True
    try:
        await _fence(projector_pool)
        # No events — drain must return (not spin) within a generous timeout.
        await asyncio.wait_for(worker.drain(), timeout=1.0)
        # fold_once on empty DB must return processed=0.
        r = await worker.fold_once()
        assert r["processed"] == 0
    finally:
        await worker.release_lock()
