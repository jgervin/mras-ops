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
