"""Fleet registry reads (P1): parent-scoped keyset lists, §5.1 detail, audit,
unresolved devices (spec §5.2 P1; decisions D1/D2/D9/D10).

Idioms follow src/godview/systems.py: plain SQL, dict rows, ::text enum casts,
::float8 numeric casts, server-side counts, LIMIT n+1 keyset probes. Every list
is parent-scoped and bounded (spec §6) — there is no unscoped device list.
"""
import json
import uuid

from src.godview.paging import decode_cursor, encode_cursor
from src.registry.paging import decode_name_cursor, page

_ENVELOPE = "counts/items/next_cursor"   # documented shape; see plan Interfaces


async def list_organizations(conn, *, cursor=None, limit=50) -> dict:
    total = await conn.fetchval("SELECT count(*) FROM organizations")
    cur_name, cur_id = decode_name_cursor(cursor)
    rows = await conn.fetch(
        """
        SELECT o.id, o.name, o.organization_type::text AS organization_type,
               o.status::text AS status, o.parent_organization_id
        FROM organizations o
        WHERE ($1::text IS NULL OR (COALESCE(o.name,''), o.id) > ($1::text, $2::uuid))
        ORDER BY COALESCE(o.name,'') ASC, o.id ASC
        LIMIT $3
        """,
        cur_name, cur_id, limit + 1)
    items, next_cursor = page(rows, limit)
    return {"counts": {"total": total}, "items": items, "next_cursor": next_cursor}


async def list_locations(conn, *, parent_id=None, cursor=None, limit=50) -> dict:
    """parent_id None = root level (parent_location_id IS NULL)."""
    total = await conn.fetchval(
        "SELECT count(*) FROM locations "
        "WHERE ($1::uuid IS NULL AND parent_location_id IS NULL) OR parent_location_id = $1",
        parent_id)
    cur_name, cur_id = decode_name_cursor(cursor)
    rows = await conn.fetch(
        """
        SELECT l.id, l.name, l.location_type::text AS location_type, l.status::text AS status,
               (SELECT count(*) FROM locations c WHERE c.parent_location_id = l.id) AS child_location_count,
               (SELECT count(*) FROM systems s WHERE s.location_id = l.id) AS system_count
        FROM locations l
        WHERE (($1::uuid IS NULL AND l.parent_location_id IS NULL) OR l.parent_location_id = $1)
          AND ($2::text IS NULL OR (COALESCE(l.name,''), l.id) > ($2::text, $3::uuid))
        ORDER BY COALESCE(l.name,'') ASC, l.id ASC
        LIMIT $4
        """,
        parent_id, cur_name, cur_id, limit + 1)
    items, next_cursor = page(rows, limit)
    return {"counts": {"total": total}, "items": items, "next_cursor": next_cursor}


async def list_systems(conn, *, location_id, cursor=None, limit=50) -> dict:
    total = await conn.fetchval("SELECT count(*) FROM systems WHERE location_id = $1", location_id)
    cur_name, cur_id = decode_name_cursor(cursor)
    rows = await conn.fetch(
        """
        SELECT s.id, s.name, s.system_type::text AS system_type, s.status::text AS status,
               (SELECT count(*) FROM cameras c  WHERE c.system_id = s.id)
             + (SELECT count(*) FROM displays d WHERE d.system_id = s.id) AS device_count
        FROM systems s
        WHERE s.location_id = $1
          AND ($2::text IS NULL OR (COALESCE(s.name,''), s.id) > ($2::text, $3::uuid))
        ORDER BY COALESCE(s.name,'') ASC, s.id ASC
        LIMIT $4
        """,
        location_id, cur_name, cur_id, limit + 1)
    items, next_cursor = page(rows, limit)
    return {"counts": {"total": total}, "items": items, "next_cursor": next_cursor}


async def list_screen_groups(conn, *, system_id, cursor=None, limit=50) -> dict:
    total = await conn.fetchval("SELECT count(*) FROM screen_groups WHERE system_id = $1", system_id)
    cur_name, cur_id = decode_name_cursor(cursor)
    rows = await conn.fetch(
        """
        SELECT g.id, g.name, g.group_type::text AS group_type, g.status::text AS status,
               (SELECT count(*) FROM cameras c  WHERE c.screen_group_id = g.id)
             + (SELECT count(*) FROM displays d WHERE d.screen_group_id = g.id) AS device_count
        FROM screen_groups g
        WHERE g.system_id = $1
          AND ($2::text IS NULL OR (COALESCE(g.name,''), g.id) > ($2::text, $3::uuid))
        ORDER BY COALESCE(g.name,'') ASC, g.id ASC
        LIMIT $4
        """,
        system_id, cur_name, cur_id, limit + 1)
    items, next_cursor = page(rows, limit)
    return {"counts": {"total": total}, "items": items, "next_cursor": next_cursor}


_DEVICE_SCOPE = ("(($1::uuid IS NULL OR {t}.system_id = $1) "
                 "AND ($2::uuid IS NULL OR {t}.screen_group_id = $2))")


async def list_cameras(conn, *, system_id=None, screen_group_id=None, cursor=None, limit=50) -> dict:
    """Route enforces exactly-one-scope (delta 3); function accepts either."""
    scope = _DEVICE_SCOPE.format(t="c")
    total = await conn.fetchval(
        f"SELECT count(*) FROM cameras c WHERE {scope}", system_id, screen_group_id)
    cur_name, cur_id = decode_name_cursor(cursor)
    rows = await conn.fetch(
        f"""
        SELECT c.id, c.name, c.status::text AS status, c.camera_role::text AS camera_role,
               c.failover_eligible, c.screen_group_id, c.screen_id,
               COALESCE((
                   SELECT e.payload->>'to' FROM events e
                   WHERE e.event_type = 'camera_duty'
                     AND e.payload->>'camera_id' = c.id::text
                   ORDER BY e.id DESC LIMIT 1
               ), 'unknown') AS effective_duty,
               c.last_seen_at
        FROM cameras c
        WHERE {scope}
          AND ($3::text IS NULL OR (COALESCE(c.name,''), c.id) > ($3::text, $4::uuid))
        ORDER BY COALESCE(c.name,'') ASC, c.id ASC
        LIMIT $5
        """,
        system_id, screen_group_id, cur_name, cur_id, limit + 1)
    items, next_cursor = page(rows, limit)
    return {"counts": {"total": total}, "items": items, "next_cursor": next_cursor}


async def list_displays(conn, *, system_id=None, screen_group_id=None, cursor=None, limit=50) -> dict:
    scope = _DEVICE_SCOPE.format(t="d")
    total = await conn.fetchval(
        f"SELECT count(*) FROM displays d WHERE {scope}", system_id, screen_group_id)
    cur_name, cur_id = decode_name_cursor(cursor)
    rows = await conn.fetch(
        f"""
        SELECT d.id, d.name, d.status::text AS status, d.display_role::text AS display_role,
               d.screen_group_id, d.screen_id, d.last_seen_at
        FROM displays d
        WHERE {scope}
          AND ($3::text IS NULL OR (COALESCE(d.name,''), d.id) > ($3::text, $4::uuid))
        ORDER BY COALESCE(d.name,'') ASC, d.id ASC
        LIMIT $5
        """,
        system_id, screen_group_id, cur_name, cur_id, limit + 1)
    items, next_cursor = page(rows, limit)
    return {"counts": {"total": total}, "items": items, "next_cursor": next_cursor}


# --- §5.1 detail --------------------------------------------------------------
# Table-driven over STATIC SQL: (sql, identity keys, config keys, extra state keys).
# created_at/updated_at are always STATE (spec §5.1). jsonb comes back from asyncpg
# as str (no codec installed in this app) -> parsed here so config is an object.

_JSONB_KEYS = {"metadata", "config", "calibration"}

_DETAIL = {
    "organization": (
        "SELECT id, parent_organization_id, name, organization_type::text AS organization_type, "
        "metadata, status::text AS status, created_at, updated_at "
        "FROM organizations WHERE id = $1",
        ("id", "parent_organization_id"),
        ("name", "organization_type", "metadata", "status"),
        (),
    ),
    "location": (
        "SELECT id, parent_location_id, name, location_type::text AS location_type, "
        "country, region, state, city, address, lat::float8 AS lat, lng::float8 AS lng, "
        "timezone, metadata, status::text AS status, created_at, updated_at "
        "FROM locations WHERE id = $1",
        ("id", "parent_location_id"),
        ("name", "location_type", "country", "region", "state", "city", "address",
         "lat", "lng", "timezone", "metadata", "status"),
        (),
    ),
    "system": (
        "SELECT id, organization_id, location_id, name, system_type::text AS system_type, "
        "zone, floor, lat::float8 AS lat, lng::float8 AS lng, timezone, config, "
        "status::text AS status, created_at, updated_at "
        "FROM systems WHERE id = $1",
        ("id", "organization_id", "location_id"),
        ("name", "system_type", "zone", "floor", "lat", "lng", "timezone", "config", "status"),
        (),
    ),
    "screen_group": (
        "SELECT id, system_id, location_id, name, group_type::text AS group_type, "
        "metadata, status::text AS status, created_at, updated_at "
        "FROM screen_groups WHERE id = $1",
        ("id", "system_id", "location_id"),
        ("name", "group_type", "metadata", "status"),
        (),
    ),
    "camera": (
        "SELECT c.id, c.device_id, c.system_id, c.location_id, c.screen_id, c.name, "
        "c.camera_role::text AS camera_role, c.failover_eligible, c.screen_group_id, "
        "c.stream_url, c.calibration, c.status::text AS status, c.last_seen_at, "
        "c.created_at, c.updated_at, "
        "COALESCE((SELECT e.payload->>'to' FROM events e "
        "          WHERE e.event_type = 'camera_duty' "
        "            AND e.payload->>'camera_id' = c.id::text "
        "          ORDER BY e.id DESC LIMIT 1), 'unknown') AS effective_duty "
        "FROM cameras c WHERE c.id = $1",
        ("id", "device_id", "system_id", "location_id", "screen_id"),
        ("name", "camera_role", "failover_eligible", "screen_group_id",
         "stream_url", "calibration", "status"),
        ("last_seen_at", "effective_duty"),
    ),
    "display": (
        "SELECT id, device_id, system_id, location_id, screen_id, name, "
        "display_role::text AS display_role, screen_group_id, resolution_width, "
        "resolution_height, calibration, status::text AS status, last_seen_at, "
        "created_at, updated_at "
        "FROM displays WHERE id = $1",
        ("id", "device_id", "system_id", "location_id", "screen_id"),
        ("name", "display_role", "screen_group_id", "resolution_width",
         "resolution_height", "calibration", "status"),
        ("last_seen_at",),
    ),
}


def _val(row, key):
    v = row[key]
    if isinstance(v, uuid.UUID):
        return str(v)
    if key in _JSONB_KEYS and isinstance(v, str):
        return json.loads(v)
    return v


async def get_detail(conn, object_type: str, object_id) -> dict | None:
    sql, identity_keys, config_keys, state_keys = _DETAIL[object_type]
    row = await conn.fetchrow(sql, object_id)
    if row is None:
        return None
    return {
        "object_type": object_type,
        "identity": {k: _val(row, k) for k in identity_keys},
        "config": {k: _val(row, k) for k in config_keys},
        "state": {k: _val(row, k) for k in (*state_keys, "created_at", "updated_at")},
    }
