"""Admin camera-registry updates (TODO-8 Phase C, spec decision 12).

patch_camera applies a partial update to exactly the three admin-writable
fields (camera_role, status, failover_eligible) and journals the change as a
`camera_admin` event IN THE SAME TRANSACTION — an audited write either fully
happens (row + journal) or not at all. Identity columns (id, name, device_id)
are never writable here (spec §2 invariant). Enum values travel as text with
server-side casts, so pooled connections opened before migration 027's
ALTER TYPE need no client-side enum-codec refresh.
"""
import json
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

# cameras.status is device_status (NOT lifecycle_status): no 'planned'/'inactive'.
class CameraPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")  # decision 12: no other fields writable
    camera_role: Optional[Literal["detection", "enrollment", "audience_measurement",
                                  "security_context", "standby"]] = None
    status: Optional[Literal["active", "degraded", "offline", "retired"]] = None
    failover_eligible: Optional[bool] = None


_RETURNING = ("id, name, system_id, screen_group_id, camera_role::text AS camera_role, "
              "status::text AS status, failover_eligible, updated_at")


async def patch_camera(conn, camera_id: uuid.UUID, fields: dict):
    """Apply an already-schema-validated {field: value} patch. None = unknown id."""
    async with conn.transaction():
        before = await conn.fetchrow(
            "SELECT camera_role::text AS camera_role, status::text AS status, "
            "failover_eligible, system_id FROM cameras WHERE id = $1 FOR UPDATE",
            camera_id)
        if before is None:
            return None
        row = await conn.fetchrow(
            "UPDATE cameras SET "
            "  camera_role       = COALESCE($2::camera_role, camera_role), "
            "  status            = COALESCE($3::device_status, status), "
            "  failover_eligible = COALESCE($4::boolean, failover_eligible), "
            "  updated_at        = now() "
            "WHERE id = $1 RETURNING " + _RETURNING,
            camera_id, fields.get("camera_role"), fields.get("status"),
            fields.get("failover_eligible"))
        changes = {k: {"from": before[k], "to": row[k]} for k in fields}
        await conn.execute(
            "INSERT INTO events (trigger_id, service, event_type, status, payload, "
            "                    system_id, camera_id) "
            "VALUES ($1, 'mras-ops', 'camera_admin', 'success', $2::jsonb, $3, $4)",
            uuid.uuid4(),
            json.dumps({"camera_id": str(camera_id), "changes": changes}),
            before["system_id"], camera_id)
    return dict(row)
