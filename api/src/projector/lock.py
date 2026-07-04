"""T5 — Postgres advisory-lock single-writer guard.

Session-scoped ``pg_try_advisory_lock``: the lock lives on the connection and is
released explicitly or when that connection ends. The worker must hold it on a
DEDICATED connection kept for its whole lifetime (never returned to the pool, or
the lock frees). Do not "optimize" it onto a pooled batch connection.
"""


async def try_acquire(conn, key: int) -> bool:
    """Non-blocking acquire. True if this session now holds the lock, else False."""
    return await conn.fetchval("SELECT pg_try_advisory_lock($1)", key)


async def release(conn, key: int) -> bool:
    """Release one hold of the lock on this session. True if a lock was released."""
    return await conn.fetchval("SELECT pg_advisory_unlock($1)", key)


class AdvisoryLock:
    """Async context manager wrapping try_acquire/release on a caller's connection.

    Non-blocking: on enter it attempts the lock and records ``acquired``; the
    caller decides whether to proceed. On exit it releases only if it acquired.
    """

    def __init__(self, conn, key: int):
        self._conn = conn
        self._key = key
        self._acquired = False

    @property
    def acquired(self) -> bool:
        return self._acquired

    async def __aenter__(self) -> "AdvisoryLock":
        self._acquired = await try_acquire(self._conn, self._key)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._acquired:
            await release(self._conn, self._key)
            self._acquired = False
