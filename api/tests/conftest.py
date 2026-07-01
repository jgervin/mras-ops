"""Shared throwaway-Postgres fixture for the projector real-DB tests.

Mirrors the Style-B pattern in
/Users/jn/code/mras-ops/tests/test_schema_godview.py: create a fresh database,
apply every db/migrations/*.sql in sorted order, yield an asyncpg pool, drop on
teardown. No live services — the projector plumbing is exercised offline.

Requires the dockerized Postgres running:
    cd /Users/jn/code/mras-ops && docker compose up -d postgres
"""
import glob
import os
import pathlib

import asyncpg
import pytest

# api/tests -> parents[2] is the repo (worktree) root that holds db/migrations.
MIGRATIONS = sorted(
    glob.glob(str(pathlib.Path(__file__).resolve().parents[2] / "db" / "migrations" / "*.sql"))
)
ADMIN_DSN = os.environ.get("ADMIN_DATABASE_URL", "postgresql://mras:mras@localhost:5432/postgres")
TEST_DB = "mras_projector_test"
TEST_DSN = "postgresql://mras:mras@localhost:5432/" + TEST_DB


@pytest.fixture(scope="module")
async def projector_pool():
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {TEST_DB}")
    await admin.close()

    setup = await asyncpg.connect(TEST_DSN)
    for path in MIGRATIONS:
        await setup.execute(pathlib.Path(path).read_text())
    await setup.close()

    pool = await asyncpg.create_pool(TEST_DSN, min_size=2, max_size=5)
    yield pool
    await pool.close()

    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.close()


@pytest.fixture(scope="module")
async def dedicated_conn_factory():
    """Yield a factory that opens STANDALONE (non-pooled) connections to the test DB.

    The worker holds its advisory lock on a dedicated connection kept for its whole
    lifetime — a pooled connection would free the session lock on release (asyncpg
    runs pg_advisory_unlock_all() on reset). These tests need the same: real
    standalone connections, closed on teardown.
    """
    conns = []

    async def _make():
        conn = await asyncpg.connect(TEST_DSN)
        conns.append(conn)
        return conn

    yield _make
    for conn in conns:
        await conn.close()
