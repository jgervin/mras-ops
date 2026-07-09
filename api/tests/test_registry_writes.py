"""Shared write plumbing: SET-clause builder (null-out capable), null policy,
same-system guard, registry_admin journaling (spec D6/D10), and the I-1
audit-noise filter (orchestrator amendment 2026-07-08)."""
import json
import uuid

import pytest

from src.registry.writes import (SemanticError, build_set_clause, diff_changes,
                                 ensure_same_system, journal_registry_admin, reject_nulls)
from tests.registry_seed import org_loc_sys, screen_group

pytestmark = pytest.mark.usefixtures("godview_isolate")


# --- pure helpers (no DB) ------------------------------------------------------

def test_build_set_clause_binds_values_and_casts():
    sql, args = build_set_clause({"status": "offline", "screen_group_id": None},
                                 {"status": "::device_status"})
    assert sql == "status = $2::device_status, screen_group_id = $3"
    assert args == ["offline", None]            # explicit NULL rides through (ungroup)


def test_build_set_clause_serializes_jsonb_fields():
    sql, args = build_set_clause({"calibration": {"cam_index": 2}}, {"calibration": "::jsonb"})
    assert sql == "calibration = $2::jsonb"
    assert args == ['{"cam_index": 2}']


def test_reject_nulls_policy():
    reject_nulls({"name": None, "screen_group_id": None}, frozenset({"name", "screen_group_id"}))
    with pytest.raises(SemanticError) as exc:
        reject_nulls({"status": None}, frozenset({"name"}))
    assert "status cannot be null" in str(exc.value)


def test_diff_changes_filters_from_equal_to():
    before = {"name": "Old", "status": "active"}
    row = {"name": "Old", "status": "offline"}          # name resent unchanged, status changed
    changes = diff_changes(before, row, ["name", "status"])
    assert changes == {"status": {"from": "active", "to": "offline"}}


def test_diff_changes_all_unchanged_is_empty():
    before = {"name": "Old", "status": "active"}
    row = {"name": "Old", "status": "active"}
    assert diff_changes(before, row, ["name", "status"]) == {}


# --- DB helpers ----------------------------------------------------------------

async def test_ensure_same_system_guard(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    _, _, other_sid = await org_loc_sys(projector_pool, sys_name="Sys2")
    gid = await screen_group(projector_pool, sid)
    async with projector_pool.acquire() as conn:
        await ensure_same_system(conn, gid, sid)                    # ok
        with pytest.raises(SemanticError, match="same system"):
            await ensure_same_system(conn, gid, other_sid)          # D6 cross-system
        with pytest.raises(SemanticError, match="unknown screen_group_id"):
            await ensure_same_system(conn, uuid.uuid4(), sid)


async def test_journal_registry_admin_shape(projector_pool):
    _, _, sid = await org_loc_sys(projector_pool)
    oid = uuid.uuid4()
    async with projector_pool.acquire() as conn:
        await journal_registry_admin(
            conn, object_type="display", object_id=oid, action="update",
            changes={"name": {"from": "Old", "to": "New"}}, system_id=sid)
    ev = await projector_pool.fetchrow(
        "SELECT service, status, system_id, payload FROM events "
        "WHERE event_type = 'registry_admin' ORDER BY id DESC LIMIT 1")
    assert ev["service"] == "mras-ops" and ev["status"] == "success"
    assert ev["system_id"] == sid
    payload = json.loads(ev["payload"])
    assert payload == {"object_type": "display", "object_id": str(oid),
                       "action": "update", "changes": {"name": {"from": "Old", "to": "New"}}}
