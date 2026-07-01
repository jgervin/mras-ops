"""T5 — Advisory-lock single-writer guard (pg_try_advisory_lock, session-scoped).

Only one projector may write. The lock is held on a dedicated connection for the
worker's lifetime; releasing (or returning the conn to the pool) frees it.
"""
from src.projector.lock import try_acquire, release, AdvisoryLock

KEY = 20260701


async def test_second_holder_is_blocked_then_acquires_after_release(projector_pool):
    async with projector_pool.acquire() as c1, projector_pool.acquire() as c2:
        assert await try_acquire(c1, KEY) is True
        # contended: a second session cannot take the same key
        assert await try_acquire(c2, KEY) is False
        # once the first releases, the second can acquire
        assert await release(c1, KEY) is True
        assert await try_acquire(c2, KEY) is True
        await release(c2, KEY)


async def test_context_manager_acquires_and_releases(projector_pool):
    async with projector_pool.acquire() as c1, projector_pool.acquire() as c2:
        async with AdvisoryLock(c1, KEY) as lock:
            assert lock.acquired is True
            assert await try_acquire(c2, KEY) is False  # held by c1 inside the block
        # exiting the context released it
        assert await try_acquire(c2, KEY) is True
        await release(c2, KEY)


async def test_context_manager_reports_not_acquired_when_contended(projector_pool):
    async with projector_pool.acquire() as c1, projector_pool.acquire() as c2:
        assert await try_acquire(c1, KEY) is True
        async with AdvisoryLock(c2, KEY) as lock:
            assert lock.acquired is False  # c1 already holds it
        await release(c1, KEY)
