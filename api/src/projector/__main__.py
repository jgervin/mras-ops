"""T13 — Module entrypoint: ``python -m src.projector``.

Mirrors the repo's script precedent (scripts/purge_auto_embeddings.py): a pure
async ``_main()`` that builds real resources + a thin ``main()`` wrapper. It opens
the fold pool AND a DEDICATED lock connection (asyncpg.connect — standalone, never
pooled, so the session advisory lock is never freed by a pool reset), wires
SIGTERM/SIGINT to the worker's stop event for a graceful drain, runs the loop, and
closes both resources on exit.

Run in-container as: ``python -m src.projector`` (WORKDIR /app, import path src.*).
"""
import asyncio
import signal

import asyncpg

from src.projector.config import ProjectorConfig
from src.projector.worker import ProjectorWorker


async def _main():
    cfg = ProjectorConfig.from_env()
    pool = await asyncpg.create_pool(cfg.database_url)
    lock_conn = await asyncpg.connect(cfg.database_url)  # DEDICATED — never pooled
    worker = ProjectorWorker(pool, lock_conn, cfg)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:  # pragma: no cover — non-Unix
            pass

    try:
        await worker.run()
    finally:
        await lock_conn.close()
        await pool.close()


def main():
    asyncio.run(_main())


if __name__ == "__main__":
    main()
