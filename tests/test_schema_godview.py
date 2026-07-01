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
    assert await _enum_values(schema_db, "embedding_status") == ["pending", "active", "rejected", "expired", "deleted"]
    assert await _enum_values(schema_db, "embedding_type") == ["face"]


async def test_role_label_enum_has_eight_canonical_roles(schema_db):
    roles = await _enum_values(schema_db, "role_label")
    assert len(roles) == 8
    assert roles == [
        "Customer.OptInIdentified", "Customer.Anonymous", "Customer.Blocklisted",
        "Host.IT", "Advertiser.ReadOnly", "AgencyOfRecord.Standard",
        "Operator.SystemAdmin", "Operator.SeniorSystemAdmin",
    ]


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


async def _has_unique(conn, table):
    rows = await conn.fetch(
        """
        SELECT array_agg(a.attname ORDER BY a.attnum) AS cols
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN unnest(c.conkey) AS k(attnum) ON true
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
        WHERE t.relname = $1 AND c.contype = 'u'
        GROUP BY c.oid
        """,
        table,
    )
    return [sorted(r["cols"]) for r in rows]


async def test_people_tables(schema_db):
    for t in ("subject_profiles", "identity_enrollments", "subject_embeddings",
              "subject_observations", "identity_matches", "observation_tracks",
              "subject_profile_merges", "blocklist_entries"):
        assert await _table_exists(schema_db, t), f"missing {t}"
    # Decision 4: no legacy identity tables
    for absent in ("identities", "identity_embeddings"):
        assert not await _table_exists(schema_db, absent), f"{absent} must not exist"
    # Decision 5: embedding lifecycle gate for the reconciler
    assert await _column_type(schema_db, "subject_embeddings", "qdrant_point_id") == "text"
    # Verify subject_observations has UNIQUE(event_id) constraint
    assert ["event_id"] in await _has_unique(schema_db, "subject_observations")


async def _fk_target(conn, table, column):
    return await conn.fetchval(
        """
        SELECT ccu.table_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu ON kcu.constraint_name = tc.constraint_name
        JOIN information_schema.constraint_column_usage ccu ON ccu.constraint_name = tc.constraint_name
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name = $1 AND kcu.column_name = $2
        LIMIT 1
        """,
        table, column,
    )


async def test_creative_tables(schema_db):
    for t in ("media_assets", "campaigns", "campaign_rules", "components",
              "ads", "ad_creatives", "creative_approvals"):
        assert await _table_exists(schema_db, t), f"missing {t}"
    # old serial-id campaigns shell must be gone (Decision 6) — new one is uuid
    assert await _column_type(schema_db, "campaigns", "id") == "uuid"
    # deferred asset FK now resolved
    assert await _fk_target(schema_db, "subject_profiles", "primary_photo_asset_id") == "media_assets"
    assert await _fk_target(schema_db, "subject_embeddings", "source_asset_id") == "media_assets"


async def test_runs_tables(schema_db):
    for t in ("personalization_decisions", "composition_runs", "ad_runs",
              "playbacks", "viewer_exposures", "model_runs"):
        assert await _table_exists(schema_db, t), f"missing {t}"
    # Decision 12: target watch is a nullable bool; bystanders use probability
    assert await _column_type(schema_db, "ad_runs", "target_watched") == "boolean"
    assert await _column_type(schema_db, "viewer_exposures", "watch_probability") == "numeric"


async def test_idempotency_keys(schema_db):
    # Decision 3: projector replay-safety enforced by unique natural keys
    assert ["trigger_id"] in await _has_unique(schema_db, "ad_runs")
    assert ["display_id", "trigger_id"] in await _has_unique(schema_db, "playbacks")


async def test_events_scope(schema_db):
    assert await _table_exists(schema_db, "events")
    # Decision 8: events keeps an integer cursor, NOT a uuid PK
    assert await _column_type(schema_db, "events", "id") == "bigint"
    # Decision 2: first-class scope columns on the journal
    for col in ("organization_id", "location_id", "system_id", "display_id", "camera_id",
                "subject_profile_id", "ad_run_id"):
        assert await _column_type(schema_db, "events", col) == "uuid", f"events.{col} missing"
    assert await _fk_target(schema_db, "events", "system_id") == "systems"


async def test_legacy_absent(schema_db):
    for absent in ("identities", "identity_embeddings", "users", "roles", "permissions"):
        assert not await _table_exists(schema_db, absent), f"{absent} must not exist"


async def _index_names(conn, table):
    rows = await conn.fetch(
        "SELECT indexname FROM pg_indexes WHERE tablename = $1", table
    )
    return {r["indexname"] for r in rows}


async def test_feed_indexes(schema_db):
    ev = await _index_names(schema_db, "events")
    assert "events_system_ts_idx" in ev
    assert "events_location_ts_idx" in ev
    assert "events_ad_run_idx" in ev
    assert "events_ts_desc_idx" in ev
    assert "events_trigger_id_idx" in ev
    se = await _index_names(schema_db, "subject_embeddings")
    assert "subject_embeddings_active_idx" in se


async def test_summary_scope_columns(schema_db):
    # Decision 2 / criterion 7.4: scope columns on every summary table
    for t in ("ad_runs", "playbacks", "viewer_exposures", "subject_observations"):
        assert await _column_type(schema_db, t, "system_id") == "uuid", f"{t}.system_id missing"
        assert await _column_type(schema_db, t, "location_id") == "uuid", f"{t}.location_id missing"
        assert await _fk_target(schema_db, t, "system_id") == "systems", f"{t}.system_id FK"
    # display-bearing summary tables
    for t in ("ad_runs", "playbacks", "viewer_exposures"):
        assert await _column_type(schema_db, t, "display_id") == "uuid", f"{t}.display_id missing"
    # observation is camera-scoped, not display-scoped
    assert await _column_type(schema_db, "subject_observations", "camera_id") == "uuid"


async def test_enum_rejects_invalid_value(schema_db):
    import asyncpg
    with pytest.raises(asyncpg.exceptions.DataError):
        await schema_db.execute(
            "INSERT INTO organizations (name, organization_type) VALUES ('x', 'bogus')"
        )


async def test_projector_idempotency_keys(schema_db):
    # 018: natural unique keys on the 5 projector-written tables + NOT NULL discriminators
    # observation_tracks: keyed on the RAW camera screen_id string (not the resolved uuid)
    assert await _column_type(schema_db, "observation_tracks", "camera_screen_id") == "text"
    assert await _column_is_nullable(schema_db, "observation_tracks", "camera_screen_id") is False
    assert await _column_is_nullable(schema_db, "observation_tracks", "camera_track_id") is False
    assert ["camera_screen_id", "camera_track_id"] in await _has_unique(schema_db, "observation_tracks")
    # identity_matches: N ranked candidates per observation
    assert await _column_is_nullable(schema_db, "identity_matches", "rank") is False
    assert ["rank", "subject_observation_id"] in await _has_unique(schema_db, "identity_matches")
    # personalization_decisions: one decision per source event
    assert await _column_is_nullable(schema_db, "personalization_decisions", "event_id") is False
    assert ["event_id"] in await _has_unique(schema_db, "personalization_decisions")
    # composition_runs: one composition per trigger
    assert await _column_is_nullable(schema_db, "composition_runs", "trigger_id") is False
    assert ["trigger_id"] in await _has_unique(schema_db, "composition_runs")
    # viewer_exposures: one exposure per (ad_run, observed viewer)
    assert await _column_is_nullable(schema_db, "viewer_exposures", "subject_observation_id") is False
    assert ["ad_run_id", "subject_observation_id"] in await _has_unique(schema_db, "viewer_exposures")
