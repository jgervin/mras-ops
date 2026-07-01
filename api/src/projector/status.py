"""T11 — Projector lag/status: a read-only view of pipeline health.

The api reads the shared Postgres directly (no RPC to the worker): the singleton
``projector_state`` row plus ``max(events.id)``. From those it derives the
backlog (unfolded events), the wall-clock lag behind the newest folded event, and
a coarse health level the God View panel / orchestrator health checks key on.
"""


def health_level(lag_seconds, cfg) -> str:
    """ok / warn / crit from the LAG_WARN_S / LAG_CRIT_S thresholds.

    No events yet (lag_seconds is None) is healthy — nothing to be behind on."""
    if lag_seconds is None:
        return "ok"
    if lag_seconds >= cfg.lag_crit_s:
        return "crit"
    if lag_seconds >= cfg.lag_warn_s:
        return "warn"
    return "ok"


async def get_projector_status(conn, cfg) -> dict:
    row = await conn.fetchrow(
        """
        SELECT ps.cursor,
               ps.last_event_ts,
               ps.updated_at,
               ps.projector_ver,
               (SELECT COALESCE(max(id), 0) FROM events)          AS max_event_id,
               EXTRACT(epoch FROM now() - ps.last_event_ts)::float8 AS lag_seconds
        FROM projector_state ps
        WHERE ps.id = 1
        """
    )
    if row is None:
        raise RuntimeError(
            "projector_state not initialized (migration 019 not applied)"
        )
    cursor = row["cursor"]
    max_event_id = row["max_event_id"]
    lag_seconds = None if row["lag_seconds"] is None else float(row["lag_seconds"])
    return {
        "cursor": cursor,
        "last_event_ts": row["last_event_ts"],
        "updated_at": row["updated_at"],
        "projector_ver": row["projector_ver"],
        "backlog": max_event_id - cursor,
        "lag_seconds": lag_seconds,
        "health": health_level(lag_seconds, cfg),
    }
