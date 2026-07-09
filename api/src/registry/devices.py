"""Admin display writes (fleet P2, spec D6/D10).

Mirrors src/cameras.py: strict pydantic extra="forbid", FOR UPDATE + UPDATE +
journal in ONE transaction, enum text casts. Displays never had a legacy event
type, so everything here journals the generic registry_admin event (D10). Per
the I-1 orchestrator amendment (2026-07-08, BINDING), PATCH journaled changes
are filtered to from != to; a PATCH whose fields all resolve to no-op writes
skips the event entirely.
"""
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from src.registry.lifecycle import check_transition
from src.registry.writes import (build_set_clause, diff_changes, ensure_same_system,
                                 journal_registry_admin, reject_nulls)

_DISPLAY_ROLES = Literal["primary_ad", "secondary_ad", "ambient", "status"]
_DEVICE_STATUSES = Literal["active", "degraded", "offline", "retired"]


class DisplayPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")   # identity/state schema-rejected (D1/D2)
    name: Optional[str] = None
    display_role: Optional[_DISPLAY_ROLES] = None
    status: Optional[_DEVICE_STATUSES] = None
    screen_group_id: Optional[uuid.UUID] = None
    resolution_width: Optional[int] = None
    resolution_height: Optional[int] = None
    calibration: Optional[dict] = None


_PATCH_NULLABLE = frozenset({"name", "screen_group_id", "resolution_width", "resolution_height"})
_CASTS = {"display_role": "::display_role", "status": "::device_status", "calibration": "::jsonb"}

_RETURNING = ("id, name, system_id, screen_group_id, screen_id, "
              "display_role::text AS display_role, status::text AS status, "
              "resolution_width, resolution_height, calibration, updated_at")


async def patch_display(conn, display_id: uuid.UUID, fields: dict):
    """None = unknown id. Raises TransitionError (409) / SemanticError (422)."""
    reject_nulls(fields, _PATCH_NULLABLE)
    async with conn.transaction():
        before = await conn.fetchrow(
            "SELECT name, display_role::text AS display_role, status::text AS status, "
            "screen_group_id, resolution_width, resolution_height, calibration, system_id "
            "FROM displays WHERE id = $1 FOR UPDATE",
            display_id)
        if before is None:
            return None
        if "status" in fields:
            check_transition(before["status"], fields["status"])            # D3
        if fields.get("screen_group_id") is not None:
            await ensure_same_system(conn, fields["screen_group_id"], before["system_id"])  # D6
        set_sql, args = build_set_clause(fields, _CASTS)
        row = await conn.fetchrow(
            f"UPDATE displays SET {set_sql}, updated_at = now() "
            f"WHERE id = $1 RETURNING {_RETURNING}",
            display_id, *args)
        changes = diff_changes(before, row, fields)          # I-1: from != to only
        if changes:
            await journal_registry_admin(
                conn, object_type="display", object_id=display_id,
                action="lifecycle" if set(fields) == {"status"} else "update",
                changes=changes, system_id=before["system_id"], display_id=display_id)
    return dict(row)
