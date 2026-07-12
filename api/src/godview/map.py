"""God View Globe map read: one set-based rollup query, one row per venue.

There are no rollup/summary tables — like the dashboard, this computes on the
fly from the base tables the projector writes (ad_runs / composition_runs /
playbacks) joined to the registry (locations / systems / cameras / displays).

Encodings (spec 2026-07-11 §3, defined against what the projector actually
produces — device_status has no 'failing'; red is reserved for activity
failures, not a device status):
  worst_status        worst device status at the venue; offline > retired >
                      degraded > active
  composing_count     triggers with ad_runs.status='planned' OR an open
                      composition (queued/rendering) that has no ad_run row yet
  playing_count       triggers with ad_runs.status in (dispatched, playing) OR
                      an open playback (dispatched/started). Open playbacks are
                      only visible through their ad_run/composition trigger —
                      the composer's ad-run-less idle playbacks (which never
                      receive 'ended') can therefore never glow.
  failures_last_hour  failed ad_runs + failed compositions in the last hour
"""

_MAP_SQL = """
WITH sys AS (
    SELECT location_id, count(*) AS systems
    FROM systems
    GROUP BY location_id
),
dev AS (
    SELECT s.location_id,
           count(*) FILTER (WHERE d.kind = 'camera')  AS cameras,
           count(*) FILTER (WHERE d.kind = 'display') AS displays,
           max(CASE d.status WHEN 'offline' THEN 4 WHEN 'retired' THEN 3
                             WHEN 'degraded' THEN 2 ELSE 1 END) AS worst_rank
    FROM (
        SELECT system_id, 'camera' AS kind, status::text AS status FROM cameras
        UNION ALL
        SELECT system_id, 'display' AS kind, status::text AS status FROM displays
    ) d
    JOIN systems s ON s.id = d.system_id
    GROUP BY s.location_id
),
pb AS (
    SELECT trigger_id,
           bool_or(status IN ('dispatched','started')) AS open_playback,
           max(GREATEST(COALESCE(started_at, created_at),
                        COALESCE(ended_at, created_at), created_at)) AS last_ts
    FROM playbacks
    GROUP BY trigger_id
),
tr AS (
    -- one row per trigger: ad_runs and composition_runs are both
    -- UNIQUE(trigger_id), so the FULL JOIN is 1:1; playbacks pre-aggregated.
    SELECT COALESCE(ar.location_id, cr.location_id) AS location_id,
           ar.status::text AS ar_status,
           cr.status::text AS cr_status,
           COALESCE(pb.open_playback, false) AS open_playback,
           ar.created_at AS ar_created_at,
           GREATEST(COALESCE(ar.updated_at, '-infinity'),
                    COALESCE(cr.ended_at, cr.started_at, cr.created_at, '-infinity'),
                    COALESCE(pb.last_ts, '-infinity')) AS last_ts,
           ((ar.status = 'failed'
             AND COALESCE(ar.ended_at, ar.updated_at) >= now() - interval '1 hour')
            OR (cr.status = 'failed'
                AND COALESCE(cr.ended_at, cr.created_at) >= now() - interval '1 hour')
           ) AS failed_last_hour
    FROM ad_runs ar
    FULL JOIN composition_runs cr ON cr.trigger_id = ar.trigger_id
    LEFT JOIN pb ON pb.trigger_id = COALESCE(ar.trigger_id, cr.trigger_id)
),
act AS (
    SELECT location_id,
           count(*) FILTER (WHERE ar_status = 'planned'
                               OR (ar_status IS NULL
                                   AND cr_status IN ('queued','rendering'))) AS composing,
           count(*) FILTER (WHERE ar_status IN ('dispatched','playing')
                               OR open_playback)                             AS playing,
           count(*) FILTER (WHERE ar_created_at >= now() - interval '1 hour') AS runs_last_hour,
           count(*) FILTER (WHERE failed_last_hour)                          AS failures_last_hour,
           NULLIF(max(last_ts), '-infinity')                                 AS last_activity_at
    FROM tr
    WHERE location_id IS NOT NULL
    GROUP BY location_id
)
SELECT l.id AS location_id, l.name, l.location_type::text AS location_type,
       l.city, l.country, l.lat::float8 AS lat, l.lng::float8 AS lng,
       sys.systems,
       COALESCE(dev.cameras, 0)  AS cameras,
       COALESCE(dev.displays, 0) AS displays,
       CASE COALESCE(dev.worst_rank, 1)
            WHEN 4 THEN 'offline' WHEN 3 THEN 'retired'
            WHEN 2 THEN 'degraded' ELSE 'active' END AS worst_status,
       COALESCE(act.composing, 0)          AS composing,
       COALESCE(act.playing, 0)            AS playing,
       COALESCE(act.runs_last_hour, 0)     AS runs_last_hour,
       COALESCE(act.failures_last_hour, 0) AS failures_last_hour,
       act.last_activity_at
FROM locations l
JOIN sys ON sys.location_id = l.id
LEFT JOIN dev ON dev.location_id = l.id
LEFT JOIN act ON act.location_id = l.id
ORDER BY l.name ASC, l.id ASC
"""


async def get_map(conn) -> dict:
    rows = await conn.fetch(_MAP_SQL)
    venues = []
    for r in rows:
        venues.append({
            "location_id": r["location_id"],
            "name": r["name"],
            "location_type": r["location_type"],
            "city": r["city"],
            "country": r["country"],
            "lat": r["lat"],
            "lng": r["lng"],
            "rollup": {
                "systems": r["systems"],
                "cameras": r["cameras"],
                "displays": r["displays"],
                "worst_status": r["worst_status"],
                "active_ad_runs": r["composing"] + r["playing"],
                "composing_count": r["composing"],
                "playing_count": r["playing"],
                "runs_last_hour": r["runs_last_hour"],
                "failures_last_hour": r["failures_last_hour"],
                "last_activity_at": r["last_activity_at"],
            },
        })
    return {"venues": venues}


async def get_map_location(conn, location_id, *, limit: int = 20) -> dict | None:
    """Venue panel payload: location header, systems with nested cameras/displays,
    recent ad_runs (newest-first, bounded). Per-venue drill-down — a handful of
    rows — so bounded queries + Python grouping, like godview/systems.get_system."""
    loc = await conn.fetchrow(
        "SELECT id, name, location_type::text AS location_type, city, country, "
        "lat::float8 AS lat, lng::float8 AS lng, timezone, status::text AS status "
        "FROM locations WHERE id = $1", location_id)
    if loc is None:
        return None

    systems = [dict(r) for r in await conn.fetch(
        "SELECT id, name, zone, status::text AS status, system_type::text AS system_type "
        "FROM systems WHERE location_id = $1 ORDER BY name ASC, id ASC", location_id)]
    sys_ids = [s["id"] for s in systems]

    cams = await conn.fetch(
        "SELECT id, system_id, name, status::text AS status, screen_id, last_seen_at "
        "FROM cameras WHERE system_id = ANY($1::uuid[]) "
        "ORDER BY name ASC NULLS LAST, id ASC", sys_ids)
    disps = await conn.fetch(
        "SELECT id, system_id, name, status::text AS status, screen_id, last_seen_at "
        "FROM displays WHERE system_id = ANY($1::uuid[]) "
        "ORDER BY name ASC NULLS LAST, id ASC", sys_ids)
    by_sys_cams: dict = {}
    for c in cams:
        d = dict(c)
        by_sys_cams.setdefault(d.pop("system_id"), []).append(d)
    by_sys_disps: dict = {}
    for x in disps:
        d = dict(x)
        by_sys_disps.setdefault(d.pop("system_id"), []).append(d)
    for s in systems:
        s["cameras"] = by_sys_cams.get(s["id"], [])
        s["displays"] = by_sys_disps.get(s["id"], [])

    ad_runs = [dict(r) for r in await conn.fetch(
        "SELECT ar.id, ar.status::text AS status, ar.system_id, s.name AS system_name, "
        "ar.started_at, ar.ended_at, ar.created_at "
        "FROM ad_runs ar LEFT JOIN systems s ON s.id = ar.system_id "
        "WHERE ar.location_id = $1 "
        "ORDER BY ar.created_at DESC, ar.id DESC LIMIT $2", location_id, limit)]

    return {"location": dict(loc), "systems": systems, "ad_runs": ad_runs}
