"""Keyset paging for registry lists (fleet P1, spec D9).

Sort key is (COALESCE(name,''), id) — device names are nullable. The cursor is
"<name>|<uuid>". Names may contain '|', uuids never do, so decoding splits on
the LAST '|'. (godview/systems' private decoder splits on the first — fine for
that shipped endpoint, wrong in general; it is deliberately left untouched.)
"""
import uuid


def encode_name_cursor(name, row_id) -> str:
    return f"{name or ''}|{row_id}"


def decode_name_cursor(cursor):
    if not cursor:
        return (None, None)
    name, _, rid = cursor.rpartition("|")
    return (name, uuid.UUID(rid))


def page(rows, limit, *, name_key="name"):
    """rows were fetched with LIMIT limit+1 -> (items, next_cursor)."""
    items = [dict(r) for r in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_name_cursor(last[name_key], last["id"])
    return items, next_cursor
