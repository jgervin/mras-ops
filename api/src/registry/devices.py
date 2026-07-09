"""Admin display writes + device creation (fleet P2, spec D6/D7/D8/D10).

Mirrors src/cameras.py: strict pydantic extra="forbid", FOR UPDATE + UPDATE +
journal in ONE transaction, enum text casts. Displays never had a legacy event
type, so everything here journals the generic registry_admin event (D10).
Creates are staged (D7: status hardcoded 'offline') and mint the devices
identity row when device_id is absent (D8). Per the I-1 orchestrator amendment
(2026-07-08, BINDING), PATCH journaled changes are filtered to from != to; a
PATCH whose fields all resolve to no-op writes skips the event entirely.
"""
import json
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from src.registry.lifecycle import check_transition
from src.registry.writes import (SemanticError, build_set_clause, diff_changes,
                                 ensure_same_system, jsonable, journal_registry_admin,
                                 reject_nulls)

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


# --- creation (D7 staged offline, D8 devices identity row) ----------------------

_CAMERA_ROLES = Literal["detection", "enrollment", "audience_measurement",
                        "security_context", "standby"]


class CameraCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")   # D7: no status field at birth
    system_id: uuid.UUID
    screen_id: Optional[str] = None             # nullable for cameras (012)
    name: Optional[str] = None
    camera_role: _CAMERA_ROLES = "detection"
    failover_eligible: bool = False
    screen_group_id: Optional[uuid.UUID] = None
    stream_url: Optional[str] = None
    calibration: dict = {}
    device_id: Optional[uuid.UUID] = None       # D8: identity row minted when absent
    serial_number: Optional[str] = None
    external_device_key: Optional[str] = None


class DisplayCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    system_id: uuid.UUID
    screen_id: str                              # NOT NULL on displays (012)
    name: Optional[str] = None
    display_role: _DISPLAY_ROLES = "primary_ad"
    screen_group_id: Optional[uuid.UUID] = None
    resolution_width: Optional[int] = None
    resolution_height: Optional[int] = None
    calibration: dict = {}
    device_id: Optional[uuid.UUID] = None
    serial_number: Optional[str] = None
    external_device_key: Optional[str] = None


async def _resolve_system(conn, system_id):
    row = await conn.fetchrow("SELECT location_id FROM systems WHERE id = $1", system_id)
    if row is None:
        raise SemanticError("unknown system_id")
    return row["location_id"]


async def _resolve_device_id(conn, body, device_type: str):
    """D8: mint the devices identity row (same txn, staged offline) unless supplied."""
    if body.device_id is not None:
        if await conn.fetchval("SELECT 1 FROM devices WHERE id = $1", body.device_id) is None:
            raise SemanticError("unknown device_id")
        return body.device_id
    return await conn.fetchval(
        "INSERT INTO devices (system_id, location_id, device_type, name, "
        "                     external_device_key, serial_number, status) "
        "VALUES ($1, $2, $3::device_type, $4, $5, $6, 'offline') RETURNING id",
        body.system_id, await _resolve_system(conn, body.system_id), device_type,
        body.name or body.screen_id or device_type,
        body.external_device_key, body.serial_number)


def _birth_changes(row, fields) -> dict:
    return {f: {"from": None, "to": jsonable(row[f])} for f in fields}


_CAMERA_RETURNING = ("id, device_id, system_id, location_id, screen_id, name, "
                     "camera_role::text AS camera_role, failover_eligible, screen_group_id, "
                     "stream_url, calibration, status::text AS status, created_at, updated_at")


async def create_camera(conn, body: CameraCreate) -> dict:
    async with conn.transaction():
        location_id = await _resolve_system(conn, body.system_id)
        if body.screen_group_id is not None:
            await ensure_same_system(conn, body.screen_group_id, body.system_id)
        device_id = await _resolve_device_id(conn, body, "camera")
        row = await conn.fetchrow(
            "INSERT INTO cameras (device_id, system_id, location_id, name, camera_role, "
            "                     stream_url, screen_id, screen_group_id, failover_eligible, "
            "                     status, calibration) "
            "VALUES ($1, $2, $3, $4, $5::camera_role, $6, $7, $8, $9, 'offline', $10::jsonb) "
            "RETURNING " + _CAMERA_RETURNING,
            device_id, body.system_id, location_id, body.name, body.camera_role,
            body.stream_url, body.screen_id, body.screen_group_id, body.failover_eligible,
            json.dumps(body.calibration))
        await journal_registry_admin(
            conn, object_type="camera", object_id=row["id"], action="create",
            changes=_birth_changes(row, ("device_id", "system_id", "location_id", "screen_id",
                                         "name", "camera_role", "failover_eligible",
                                         "screen_group_id", "stream_url", "calibration", "status")),
            system_id=body.system_id, camera_id=row["id"])
    return dict(row)


_DISPLAY_CREATE_RETURNING = ("id, device_id, system_id, location_id, screen_id, name, "
                             "display_role::text AS display_role, screen_group_id, "
                             "resolution_width, resolution_height, calibration, "
                             "status::text AS status, created_at, updated_at")


async def create_display(conn, body: DisplayCreate, *, action: str = "create",
                         extra: Optional[dict] = None) -> dict:
    """action/extra let adopt_display (D11) reuse this as the single journal write
    for its action="adopt" event, instead of writing then rewriting a journal row."""
    async with conn.transaction():
        location_id = await _resolve_system(conn, body.system_id)
        if body.screen_group_id is not None:
            await ensure_same_system(conn, body.screen_group_id, body.system_id)
        device_id = await _resolve_device_id(conn, body, "display")
        row = await conn.fetchrow(
            "INSERT INTO displays (device_id, system_id, location_id, name, screen_id, "
            "                      display_role, screen_group_id, resolution_width, "
            "                      resolution_height, status, calibration) "
            "VALUES ($1, $2, $3, $4, $5, $6::display_role, $7, $8, $9, 'offline', $10::jsonb) "
            "RETURNING " + _DISPLAY_CREATE_RETURNING,
            device_id, body.system_id, location_id, body.name, body.screen_id,
            body.display_role, body.screen_group_id, body.resolution_width,
            body.resolution_height, json.dumps(body.calibration))
        await journal_registry_admin(
            conn, object_type="display", object_id=row["id"], action=action,
            changes=_birth_changes(row, ("device_id", "system_id", "location_id", "screen_id",
                                         "name", "display_role", "screen_group_id",
                                         "resolution_width", "resolution_height",
                                         "calibration", "status")),
            system_id=body.system_id, display_id=row["id"], extra=extra)
    return dict(row)
