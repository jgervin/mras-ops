"""God View main-dashboard read: O(1) payload regardless of fleet size.

Returns server-computed counts + a handful of bounded candidate rows. All
view-shaping (KPI mapping, failure merge/rank) happens in the client selectors;
this only bounds the data.
"""

_ACTIVE_STATUSES = ("composing", "dispatched", "playing")
_HEALTH_WINDOW_SECS = 60  # readings window for camera_rows


async def get_dashboard(conn) -> dict:
    fleet_rows = await conn.fetch("SELECT status::text AS status, count(*) AS n FROM systems GROUP BY status")
    counts = {r["status"]: r["n"] for r in fleet_rows}
    fleet = {
        "total": sum(counts.values()),
        "active": counts.get("active", 0),
        "degraded": counts.get("degraded", 0),
        "offline": counts.get("offline", 0),
    }
    org_count = await conn.fetchval("SELECT count(*) FROM organizations")

    active_count = await conn.fetchval(
        "SELECT count(*) FROM ad_runs WHERE status = ANY($1::ad_run_status[])", list(_ACTIVE_STATUSES))
    active_runs = [dict(r) for r in await conn.fetch(
        """
        SELECT ar.id, ar.status::text AS status, ar.started_at, ar.system_id, s.name AS system_name
        FROM ad_runs ar
        LEFT JOIN systems s ON s.id = ar.system_id
        WHERE ar.status = ANY($1::ad_run_status[])
        ORDER BY ar.started_at DESC NULLS LAST, ar.id DESC
        LIMIT 5
        """,
        list(_ACTIVE_STATUSES),
    )]

    recent_failed_runs = [dict(r) for r in await conn.fetch(
        """
        SELECT ar.id, ar.system_id, s.name AS system_name, ar.ended_at, cr.error_code
        FROM ad_runs ar
        LEFT JOIN systems s ON s.id = ar.system_id
        LEFT JOIN composition_runs cr ON cr.id = ar.composition_run_id
        WHERE ar.status = 'failed'
        ORDER BY ar.ended_at DESC NULLS LAST, ar.id DESC
        LIMIT 10
        """
    )]

    recent_health_drops = [dict(r) for r in await conn.fetch(
        """
        SELECT * FROM (
            SELECT 'device' AS kind, dhe.id, dhe.device_id AS ref_id,
                   COALESCE(cam.name, disp.name, dhe.device_id::text) AS ref_name,
                   dhe.status::text AS status,
                   COALESCE(dhe.detail->>'message', dhe.detail::text) AS detail,
                   dhe.observed_at
            FROM device_health_events dhe
            LEFT JOIN cameras cam ON cam.device_id = dhe.device_id
            LEFT JOIN displays disp ON disp.device_id = dhe.device_id
            WHERE dhe.status IN ('offline','degraded')
            UNION ALL
            SELECT 'system' AS kind, she.id, she.system_id AS ref_id,
                   s.name AS ref_name, she.status::text AS status,
                   COALESCE(she.detail->>'message', she.detail::text) AS detail,
                   she.observed_at
            FROM system_health_events she
            JOIN systems s ON s.id = she.system_id
            WHERE she.status IN ('offline','degraded')
        ) u
        ORDER BY u.observed_at DESC, u.id DESC
        LIMIT 10
        """
    )]

    camera_rows = [dict(r) for r in await conn.fetch(
        """
        SELECT agg.camera_id, c.name, s.name AS system_name, c.status::text AS status,
               agg.face_count, agg.confidence::float8 AS confidence
        FROM (
            SELECT so.camera_id,
                   count(*) AS face_count,
                   COALESCE(avg(so.face_quality_score), 0) AS confidence
            FROM subject_observations so
            WHERE so.camera_id IS NOT NULL
              AND so.observed_at >= now() - make_interval(secs => $1)
            GROUP BY so.camera_id
            ORDER BY face_count DESC
            LIMIT 6
        ) agg
        JOIN cameras c ON c.id = agg.camera_id
        JOIN systems s ON s.id = c.system_id
        """,
        _HEALTH_WINDOW_SECS,
    )]

    return {
        "fleet": fleet,
        "org_count": org_count,
        "active_count": active_count,
        "active_runs": active_runs,
        "recent_failed_runs": recent_failed_runs,
        "recent_health_drops": recent_health_drops,
        "camera_rows": camera_rows,
    }
