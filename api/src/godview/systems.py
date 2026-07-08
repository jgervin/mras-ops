"""God View systems list (server counts + search + keyset) and per-system drill-down.

Counting devices and systems is unbounded, so it happens in SQL; the client
systemsWithRollup/systemsKpis selectors map the returned page/counts. Drill-down
is fetched on demand (one system's devices), so its readings use readings_for_system.
"""
import uuid

from src.godview.readings import readings_for_system


def encode_cursor_name(name: str, row_id) -> str:
    return f"{name}|{row_id}"


def _decode_name_cursor(cursor):
    """This endpoint's sort key is (name, id), so the cursor's first field is a NAME,
    not a timestamp — decode it as text (do NOT reuse paging.decode_cursor, which
    parses the first field with datetime.fromisoformat)."""
    if not cursor:
        return (None, None)
    name, _, rid = cursor.partition("|")
    return (name, uuid.UUID(rid))


async def get_systems(conn, *, search=None, cursor=None, limit=50) -> dict:
    total = await conn.fetchval("SELECT count(*) FROM systems")
    active = await conn.fetchval("SELECT count(*) FROM systems WHERE status = 'active'")
    unresolved = await conn.fetchval("SELECT count(*) FROM unresolved_devices")

    cur_name, cur_id = _decode_name_cursor(cursor)
    rows = await conn.fetch(
        """
        SELECT s.id, s.name, o.name AS org_name, l.name AS location_name,
               s.system_type::text AS system_type, s.status::text AS status,
               (SELECT count(*) FROM cameras c  WHERE c.system_id  = s.id)
             + (SELECT count(*) FROM displays d WHERE d.system_id = s.id) AS device_count
        FROM systems s
        LEFT JOIN organizations o ON o.id = s.organization_id
        LEFT JOIN locations l     ON l.id = s.location_id
        WHERE ($1::text IS NULL
               OR s.name ILIKE '%' || $1 || '%'
               OR o.name ILIKE '%' || $1 || '%'
               OR l.name ILIKE '%' || $1 || '%')
          AND ($2::text IS NULL OR (s.name, s.id) > ($2::text, $3::uuid))
        ORDER BY s.name ASC, s.id ASC
        LIMIT $4
        """,
        search, cur_name, cur_id, limit + 1,
    )
    items = [dict(r) for r in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor_name(last["name"], last["id"])
    return {
        "counts": {"total_systems": total, "active_systems": active, "unresolved_devices": unresolved},
        "items": items,
        "next_cursor": next_cursor,
    }


async def get_system(conn, system_id) -> dict | None:
    system = await conn.fetchrow(
        "SELECT id,name,status::text AS status,system_type::text AS system_type FROM systems WHERE id = $1",
        system_id)
    if system is None:
        return None
    groups = await conn.fetch(
        "SELECT id,name,group_type::text AS group_type FROM screen_groups WHERE system_id = $1 ORDER BY name",
        system_id)
    readings = await readings_for_system(conn, system_id)
    # effective_duty (TODO-8 Phase D): drill-down is one system's cameras (a handful of
    # rows); each sub-select is a single-row ordered probe of events_camera_duty_idx
    # (partial: only camera_duty rows; expression: payload->>'camera_id'; id DESC matches
    # this ORDER BY) — proportional to duty *transitions*, not traffic (027).
    cams = await conn.fetch(
        """
        SELECT c.id, c.name, c.status::text AS status, c.screen_group_id,
               c.camera_role::text AS camera_role, c.failover_eligible,
               COALESCE((
                   SELECT e.payload->>'to'
                   FROM events e
                   WHERE e.event_type = 'camera_duty'
                     AND e.payload->>'camera_id' = c.id::text
                   ORDER BY e.id DESC
                   LIMIT 1
               ), 'unknown') AS effective_duty
        FROM cameras c
        WHERE c.system_id = $1
        ORDER BY c.name
        """,
        system_id)
    displays = await conn.fetch(
        "SELECT id,name,status::text AS status,screen_id,screen_group_id FROM displays WHERE system_id = $1 ORDER BY name",
        system_id)
    cameras = []
    for c in cams:
        d = dict(c)
        r = readings.get(str(c["id"]), {"face_count": 0, "confidence": 0.0})
        d["face_count"] = r["face_count"]
        d["confidence"] = r["confidence"]
        cameras.append(d)
    return {
        "system": dict(system),
        "screen_groups": [dict(g) for g in groups],
        "cameras": cameras,
        "displays": [dict(x) for x in displays],
    }
