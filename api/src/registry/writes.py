"""Shared plumbing for registry writes (fleet P2, spec D6/D10).

Why a SET builder instead of the shipped COALESCE style: PATCH must support
explicit null-out for nullable config (ungrouping via screen_group_id=null),
which COALESCE cannot express. Column names are interpolated ONLY from
pydantic-validated field dicts (extra="forbid" models) — never raw input;
values are always bind parameters.
"""
import json
import uuid


class SemanticError(ValueError):
    """422-class semantic failure (cross-system group, unknown FK, bad null)."""


def reject_nulls(fields: dict, nullable: frozenset) -> None:
    for field, value in fields.items():
        if value is None and field not in nullable:
            raise SemanticError(f"{field} cannot be null")


def build_set_clause(fields: dict, casts: dict, *, start: int = 2):
    """-> ("col = $2::cast, col2 = $3", [v1, v2]); $1 stays the row id."""
    sets, args = [], []
    for i, (field, value) in enumerate(fields.items(), start=start):
        sets.append(f"{field} = ${i}{casts.get(field, '')}")
        args.append(json.dumps(value)
                    if field in ("calibration", "metadata", "config") and value is not None
                    else value)
    return ", ".join(sets), args


async def ensure_same_system(conn, screen_group_id, system_id) -> None:
    """Spec D6: a device's screen_group must belong to the device's system."""
    group_system = await conn.fetchval(
        "SELECT system_id FROM screen_groups WHERE id = $1", screen_group_id)
    if group_system is None:
        raise SemanticError("unknown screen_group_id")
    if group_system != system_id:
        raise SemanticError("screen_group must belong to the same system")


def jsonable(value):
    return str(value) if isinstance(value, uuid.UUID) else value


def diff_changes(before, row, fields) -> dict:
    """I-1 (orchestrator amendment, 2026-07-08, BINDING): journal only fields
    whose value actually changed (from != to). Full-form UI submits every
    config field on every PATCH; without this filter the History panel drowns
    in no-op "changes" entries. Creates are exempt (D10: creates always emit
    `from: null` for every field, regardless of value) — this helper is for
    PATCH call sites (patch_camera/patch_display) only."""
    return {k: {"from": jsonable(before[k]), "to": jsonable(row[k])}
            for k in fields if jsonable(before[k]) != jsonable(row[k])}


async def journal_registry_admin(conn, *, object_type, object_id, action, changes,
                                 system_id=None, camera_id=None, display_id=None,
                                 extra=None) -> None:
    """The one generic audit event for all NEW write endpoints (spec D10),
    emitted inside the caller's transaction (the camera_admin template)."""
    payload = {"object_type": object_type, "object_id": str(object_id),
               "action": action, "changes": changes}
    if extra:
        payload.update(extra)
    await conn.execute(
        "INSERT INTO events (trigger_id, service, event_type, status, payload, "
        "                    system_id, camera_id, display_id) "
        "VALUES ($1, 'mras-ops', 'registry_admin', 'success', $2::jsonb, $3, $4, $5)",
        uuid.uuid4(), json.dumps(payload, default=str), system_id, camera_id, display_id)
