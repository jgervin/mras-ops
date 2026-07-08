"""God View unified health/event log.

UNION of device + system health events into the prototype's LogRow shape, newest
first, keyset paginated on (observed_at, id). Device events resolve a friendly
name from the projected camera/display; jsonb detail becomes a display string.
"""
from src.godview.paging import encode_cursor, decode_cursor


async def get_events(conn, *, cursor=None, limit=50) -> dict:
    cur_ts, cur_id = decode_cursor(cursor)
    rows = await conn.fetch(
        """
        SELECT * FROM (
            SELECT 'device' AS kind, dhe.id, dhe.device_id AS ref_id,
                   COALESCE(cam.name, disp.name, dhe.device_id::text) AS ref_name,
                   dhe.status::text AS status,
                   COALESCE(dhe.detail->>'message', dhe.detail::text) AS detail,
                   dhe.observed_at
            FROM device_health_events dhe
            LEFT JOIN cameras cam  ON cam.device_id  = dhe.device_id
            LEFT JOIN displays disp ON disp.device_id = dhe.device_id
            UNION ALL
            SELECT 'system' AS kind, she.id, she.system_id AS ref_id,
                   s.name AS ref_name, she.status::text AS status,
                   COALESCE(she.detail->>'message', she.detail::text) AS detail,
                   she.observed_at
            FROM system_health_events she
            JOIN systems s ON s.id = she.system_id
        ) u
        WHERE ($1::timestamptz IS NULL OR (u.observed_at, u.id) < ($1::timestamptz, $2::uuid))
        ORDER BY u.observed_at DESC, u.id DESC
        LIMIT $3
        """,
        cur_ts, cur_id, limit + 1,
    )
    items = [dict(r) for r in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last["observed_at"], last["id"])
    return {"items": items, "next_cursor": next_cursor}
