"""God View clean-slate schema assertions.

Applies every db/migrations/*.sql into a throwaway database and asserts the
schema matches docs/superpowers/specs/2026-06-30-godview-schema-lane-a-design.md.

Requires the dockerized Postgres running:
    cd /Users/jn/code/mras-ops && docker compose up -d postgres
"""
import glob
import os
import pathlib

import asyncpg
import pytest

MIGRATIONS = sorted(
    glob.glob(str(pathlib.Path(__file__).resolve().parents[1] / "db" / "migrations" / "*.sql"))
)
ADMIN_DSN = os.environ.get("ADMIN_DATABASE_URL", "postgresql://mras:mras@localhost:5432/postgres")
TEST_DB = "mras_schema_test"
TEST_DSN = "postgresql://mras:mras@localhost:5432/" + TEST_DB

print(f"[schema_test] MIGRATIONS base = {pathlib.Path(__file__).resolve().parents[1] / 'db' / 'migrations'}")
print(f"[schema_test] Found migrations: {MIGRATIONS}")


@pytest.fixture(scope="module")
async def schema_db():
    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.execute(f"CREATE DATABASE {TEST_DB}")
    await admin.close()

    conn = await asyncpg.connect(TEST_DSN)
    for path in MIGRATIONS:
        sql = pathlib.Path(path).read_text()
        await conn.execute(sql)
    yield conn
    await conn.close()

    admin = await asyncpg.connect(ADMIN_DSN)
    await admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    await admin.close()


async def _enum_values(conn, type_name):
    rows = await conn.fetch(
        "SELECT e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
        "WHERE t.typname = $1 ORDER BY e.enumsortorder",
        type_name,
    )
    return [r["enumlabel"] for r in rows]


async def test_shared_enums_exist(schema_db):
    assert await _enum_values(schema_db, "ad_run_status") == [
        "planned", "composing", "ready", "dispatched", "playing", "completed", "failed", "canceled",
    ]
    assert await _enum_values(schema_db, "playback_status") == [
        "dispatched", "started", "ended", "failed", "interrupted", "unknown",
    ]
    assert "active" in await _enum_values(schema_db, "embedding_status")
    assert await _enum_values(schema_db, "embedding_type") == ["face"]


async def test_role_label_enum_has_eight_canonical_roles(schema_db):
    roles = await _enum_values(schema_db, "role_label")
    assert "Operator.SeniorSystemAdmin" in roles
    assert "AgencyOfRecord.Standard" in roles
    assert len(roles) == 8


async def _table_exists(conn, name):
    return await conn.fetchval(
        "SELECT to_regclass($1) IS NOT NULL", "public." + name
    )


async def _column_type(conn, table, column):
    return await conn.fetchval(
        "SELECT data_type FROM information_schema.columns "
        "WHERE table_name = $1 AND column_name = $2",
        table, column,
    )


async def _column_is_nullable(conn, table, column):
    val = await conn.fetchval(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = $1 AND column_name = $2",
        table, column,
    )
    return val == "YES"


async def test_account_tables(schema_db):
    for t in ("organizations", "organization_relationships", "user_org_scopes"):
        assert await _table_exists(schema_db, t), f"missing {t}"
    # RBAC collapsed to a thin scope map — no relational RBAC tables (Decision 1)
    for absent in ("users", "roles", "permissions", "user_memberships"):
        assert not await _table_exists(schema_db, absent), f"{absent} must not exist"
    assert await _column_type(schema_db, "user_org_scopes", "user_id") == "uuid"


async def test_physical_tables(schema_db):
    for t in ("locations", "location_participants", "systems", "devices",
              "cameras", "displays", "device_health_events", "system_health_events"):
        assert await _table_exists(schema_db, t), f"missing {t}"
    # Decision 10: runtime-string bridge lives on the device rows
    assert await _column_type(schema_db, "cameras", "screen_id") == "text"
    assert await _column_type(schema_db, "displays", "screen_id") == "text"
    # Decision 10 nullability rules: displays.screen_id NOT NULL, cameras.screen_id nullable
    assert await _column_is_nullable(schema_db, "displays", "screen_id") is False  # NOT NULL
    assert await _column_is_nullable(schema_db, "cameras", "screen_id") is True     # nullable
    # self-referential location hierarchy
    assert await _column_type(schema_db, "locations", "parent_location_id") == "uuid"
