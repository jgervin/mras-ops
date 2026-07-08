"""God View composition-activity list + filter options.

Server does the filtering and keyset pagination (unbounded over ad_runs); the
client adRunCards selector maps the returned page. Pagination orders by
(created_at, id) — created_at is NOT NULL and monotonic, so the cursor is stable.
"""
from src.godview.paging import encode_cursor, decode_cursor


async def get_ad_runs(conn, *, status=None, system_id=None, campaign_id=None,
                      since=None, cursor=None, limit=50) -> dict:
    cur_ts, cur_id = decode_cursor(cursor)
    rows = await conn.fetch(
        """
        SELECT ar.id, ar.status::text AS status, ar.started_at, ar.created_at,
               ar.system_id, s.name AS system_name, l.name AS location_name,
               ar.campaign_id, cmp.name AS campaign_name,
               (ar.personalization_decision_id IS NOT NULL) AS stage_decision,
               COALESCE(cr.status IN ('selected','rendered'), false) AS stage_composition,
               EXISTS (SELECT 1 FROM playbacks p WHERE p.ad_run_id = ar.id AND p.status = 'ended') AS stage_playback
        FROM ad_runs ar
        LEFT JOIN systems s   ON s.id = ar.system_id
        LEFT JOIN locations l ON l.id = ar.location_id
        LEFT JOIN campaigns cmp ON cmp.id = ar.campaign_id
        LEFT JOIN composition_runs cr ON cr.id = ar.composition_run_id
        WHERE ($1::ad_run_status IS NULL OR ar.status = $1::ad_run_status)
          AND ($2::uuid IS NULL OR ar.system_id = $2::uuid)
          AND ($3::uuid IS NULL OR ar.campaign_id = $3::uuid)
          AND ($4::timestamptz IS NULL OR ar.created_at >= $4::timestamptz)
          AND ($5::timestamptz IS NULL OR (ar.created_at, ar.id) < ($5::timestamptz, $6::uuid))
        ORDER BY ar.created_at DESC, ar.id DESC
        LIMIT $7
        """,
        status, system_id, campaign_id, since, cur_ts, cur_id, limit + 1,
    )
    items = [dict(r) for r in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(last["created_at"], last["id"])
    for it in items:
        it.pop("created_at", None)  # internal ordering key, not part of the contract
    return {"items": items, "next_cursor": next_cursor}


async def get_ad_run_filters(conn) -> dict:
    systems = [dict(r) for r in await conn.fetch(
        "SELECT DISTINCT s.id, s.name FROM systems s JOIN ad_runs ar ON ar.system_id = s.id ORDER BY s.name")]
    campaigns = [dict(r) for r in await conn.fetch(
        "SELECT DISTINCT c.id, c.name FROM campaigns c JOIN ad_runs ar ON ar.campaign_id = c.id ORDER BY c.name")]
    return {"systems": systems, "campaigns": campaigns}


async def get_ad_run(conn, ad_run_id) -> dict | None:
    ar = await conn.fetchrow(
        "SELECT id,trigger_id,status::text AS status,started_at,ended_at,system_id FROM ad_runs WHERE id = $1",
        ad_run_id)
    if ar is None:
        return None
    dec = await conn.fetchrow(
        """SELECT id, decision_type::text AS decision_type, decision_confidence, decision_factors
           FROM personalization_decisions
           WHERE id = (SELECT personalization_decision_id FROM ad_runs WHERE id = $1)""",
        ad_run_id)
    comp = await conn.fetchrow(
        """SELECT id, render_mode::text AS render_mode, status::text AS status,
                  error_code, error_message, used_likeness, used_voice_clone
           FROM composition_runs
           WHERE id = (SELECT composition_run_id FROM ad_runs WHERE id = $1)""",
        ad_run_id)
    plays = await conn.fetch(
        "SELECT id, status::text AS status, display_id, screen_id, error_code, error_message "
        "FROM playbacks WHERE ad_run_id = $1 ORDER BY created_at",
        ad_run_id)

    def _jsonb(row, field):
        import json
        d = dict(row)
        if isinstance(d.get(field), str):
            d[field] = json.loads(d[field])
        return d

    return {
        "ad_run": dict(ar),
        "personalization_decision": _jsonb(dec, "decision_factors") if dec else None,
        "composition_run": dict(comp) if comp else None,
        "playbacks": [dict(p) for p in plays],
    }
