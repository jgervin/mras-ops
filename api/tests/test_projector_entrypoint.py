"""T13 — Module entrypoint smoke: `python -m src.projector` wiring.

No DB, no live loop. Asserts _main() builds the config, opens the pool + a
DEDICATED lock connection (asyncpg.connect, not a pooled acquire), runs the
worker, and closes BOTH resources on exit — with fakes standing in for asyncpg
and the worker so the wiring is verified in isolation.
"""
import asyncio

import src.projector.__main__ as entry


class _FakeResource:
    def __init__(self):
        self.closed = False

    async def close(self):
        self.closed = True


def test_module_exposes_callable_entrypoints():
    assert callable(entry.main)
    assert asyncio.iscoroutinefunction(entry._main)


async def test_main_wires_pool_and_dedicated_lock_conn_and_closes(monkeypatch):
    fake_pool = _FakeResource()
    fake_lock_conn = _FakeResource()

    async def fake_create_pool(url):
        fake_pool.url = url
        return fake_pool

    async def fake_connect(url):
        fake_lock_conn.url = url
        return fake_lock_conn

    monkeypatch.setattr(entry.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(entry.asyncpg, "connect", fake_connect)

    seen = {}

    class FakeWorker:
        def __init__(self, pool, lock_conn, cfg):
            seen["pool"] = pool
            seen["lock_conn"] = lock_conn
            seen["cfg"] = cfg

        def request_stop(self):
            seen["stop_wired"] = True

        async def run(self):
            seen["ran"] = True

    monkeypatch.setattr(entry, "ProjectorWorker", FakeWorker)

    await entry._main()

    # worker got the pool for folding and a DEDICATED (asyncpg.connect) lock conn.
    assert seen["ran"] is True
    assert seen["pool"] is fake_pool
    assert seen["lock_conn"] is fake_lock_conn
    assert seen["pool"] is not seen["lock_conn"]
    # both resources closed on shutdown (no leaked lock connection).
    assert fake_pool.closed is True
    assert fake_lock_conn.closed is True
