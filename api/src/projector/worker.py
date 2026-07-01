"""T10 — Projector worker loop + lifecycle (the single-writer folder).

The worker owns TWO connections with different lifetimes:

  * a DEDICATED lock connection (``lock_conn``) held for the worker's WHOLE
    lifetime. The single-writer advisory lock is a session lock — it lives on
    this connection and frees the instant the connection ends or is returned to a
    pool (asyncpg runs ``pg_advisory_unlock_all()`` on pool release). So this
    connection is NEVER pooled and NEVER handed to the fold.
  * pooled connections (``pool``) acquired per batch for the fold's own
    transaction (upserts + back-stamp + cursor advance), released back each cycle.

Lifecycle:
  1. Passively acquire the lock. If another writer holds it, retry on the poll
     interval and DO NOT fold — never two folders at once (single-writer).
  2. While holding the lock, drain: fold a batch; if a full batch was processed
     there may be more, so loop again immediately; when a partial/empty batch
     comes back we are caught up, so sleep the poll interval.
  3. On request_stop() (SIGTERM/SIGINT wire to it) or asyncio cancellation: stop
     the loop and release the lock in the finally. The dedicated connection and
     the pool are closed by the entrypoint (src/projector/__main__.py).
"""
import asyncio

from src.projector.fold import fold_batch
from src.projector.lock import try_acquire, release
from src.projector.scope import ScopeResolver


class ProjectorWorker:
    def __init__(self, pool, lock_conn, cfg):
        self._pool = pool
        self._lock_conn = lock_conn  # DEDICATED — never returned to the pool
        self._cfg = cfg
        self._stop = asyncio.Event()
        self._holds_lock = False

    # --- lock (on the dedicated connection) ---------------------------------
    @property
    def holds_lock(self) -> bool:
        return self._holds_lock

    async def acquire_lock(self) -> bool:
        """Non-blocking acquire on the dedicated connection. Records the result."""
        self._holds_lock = await try_acquire(self._lock_conn, self._cfg.advisory_lock_key)
        return self._holds_lock

    async def _release_lock(self) -> None:
        if self._holds_lock:
            await release(self._lock_conn, self._cfg.advisory_lock_key)
            self._holds_lock = False

    async def release_lock(self) -> None:
        await self._release_lock()

    # --- fold ---------------------------------------------------------------
    async def fold_once(self) -> dict:
        """Fold one batch on a pooled connection (fold owns its own txn)."""
        async with self._pool.acquire() as conn:
            resolver = ScopeResolver(conn)
            return await fold_batch(conn, resolver, self._cfg)

    async def drain(self) -> None:
        """Fold batches until caught up (a partial batch) or a stop is requested.

        A full batch (folded+skipped == batch_size) means more may be waiting, so
        loop again immediately; anything less means we drained the backlog."""
        while not self._stop.is_set():
            res = await self.fold_once()
            if res["folded"] + res["skipped"] < self._cfg.batch_size:
                return

    # --- lifecycle ----------------------------------------------------------
    def request_stop(self) -> None:
        self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        """Sleep, but wake immediately if a stop is requested."""
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def run(self) -> None:
        poll_s = self._cfg.poll_ms / 1000
        try:
            # Passive acquisition — never fold without the lock (single-writer guard).
            while not self._stop.is_set():
                if await self.acquire_lock():
                    break
                await self._sleep(poll_s)
            # Main loop: drain the backlog, then sleep until the next poll.
            while not self._stop.is_set():
                await self.drain()
                await self._sleep(poll_s)
        finally:
            # Runs on normal stop AND on cancellation (the CancelledError keeps
            # propagating after this cleanup await completes).
            await self._release_lock()
