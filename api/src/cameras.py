"""Admin camera-registry updates (TODO-8 Phase C; extended by fleet P2, spec D2/D3/D6/D10).

patch_camera applies a partial update to the §5.1 camera CONFIG fields and
journals the change as a `camera_admin` event IN THE SAME TRANSACTION — an
audited write either fully happens (row + journal) or not at all. Identity
columns (id, device_id, system_id, screen_id) are never writable; `name` is
CONFIG since the fleet spec (admin renames are legitimate and journaled;
automation never renames). Enum values travel as text with server-side casts.
Status changes pass the D3 transition matrix (TransitionError -> 409);
screen_group re-parenting is same-system only (SemanticError -> 422).
Event type stays camera_admin — the one legacy exception to registry_admin
(fleet spec D10); change keys are additive. Per the I-1 orchestrator amendment
(2026-07-08, BINDING), journaled changes are filtered to from != to; a PATCH
whose fields all resolve to no-op writes skips the event entirely.
"""
import json
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from src.registry.lifecycle import check_transition
from src.registry.writes import build_set_clause, diff_changes, ensure_same_system, reject_nulls


# cameras.status is device_status (NOT lifecycle_status): no 'planned'/'inactive'.
class CameraPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")  # identity/state fields are schema-rejected (D1/D2)
    name: Optional[str] = None
    camera_role: Optional[Literal["detection", "enrollment", "audience_measurement",
                                  "security_context", "standby"]] = None
    status: Optional[Literal["active", "degraded", "offline", "retired"]] = None
    failover_eligible: Optional[bool] = None
    screen_group_id: Optional[uuid.UUID] = None
    stream_url: Optional[str] = None
    calibration: Optional[dict] = None


_NULLABLE = frozenset({"name", "screen_group_id", "stream_url"})
_CASTS = {"camera_role": "::camera_role", "status": "::device_status", "calibration": "::jsonb"}

_RETURNING = ("id, name, system_id, screen_group_id, camera_role::text AS camera_role, "
              "status::text AS status, failover_eligible, stream_url, calibration, updated_at")


async def patch_camera(conn, camera_id: uuid.UUID, fields: dict):
    """Apply an already-schema-validated {field: value} patch. None = unknown id.
    Raises TransitionError (409) / SemanticError (422)."""
    reject_nulls(fields, _NULLABLE)
    async with conn.transaction():
        before = await conn.fetchrow(
            "SELECT name, camera_role::text AS camera_role, status::text AS status, "
            "failover_eligible, screen_group_id, stream_url, calibration, system_id "
            "FROM cameras WHERE id = $1 FOR UPDATE",
            camera_id)
        if before is None:
            return None
        if "status" in fields:
            check_transition(before["status"], fields["status"])            # D3
        if fields.get("screen_group_id") is not None:
            await ensure_same_system(conn, fields["screen_group_id"], before["system_id"])  # D6
        set_sql, args = build_set_clause(fields, _CASTS)
        row = await conn.fetchrow(
            f"UPDATE cameras SET {set_sql}, updated_at = now() "
            f"WHERE id = $1 RETURNING {_RETURNING}",
            camera_id, *args)
        changes = diff_changes(before, row, fields)          # I-1: from != to only
        if changes:
            await conn.execute(
                "INSERT INTO events (trigger_id, service, event_type, status, payload, "
                "                    system_id, camera_id) "
                "VALUES ($1, 'mras-ops', 'camera_admin', 'success', $2::jsonb, $3, $4)",
                uuid.uuid4(),
                json.dumps({"camera_id": str(camera_id), "changes": changes}, default=str),
                before["system_id"], camera_id)
    return dict(row)
