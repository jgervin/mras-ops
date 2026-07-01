"""T11 — Projector lag/status computation.

get_projector_status(conn, cfg) reads the singleton projector_state + max(events.id)
and computes: cursor, last_event_ts, updated_at, projector_ver, backlog
(max_event_id - cursor), lag_seconds (now - last_event_ts), and a health level
(ok/warn/crit) from LAG_WARN_S / LAG_CRIT_S. Seeded against the throwaway DB.
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

from src.projector.config import ProjectorConfig
from src.projector.status import get_projector_status

CFG = ProjectorConfig.from_env({"PROJECTOR_LAG_WARN_S": "10", "PROJECTOR_LAG_CRIT_S": "60"})


async def _seed(pool, *, extra_events, cursor, last_event_age_s):
    """Insert `extra_events` events, then set the cursor + last_event_ts heartbeat."""
    for _ in range(extra_events):
        await pool.execute(
            "INSERT INTO events (trigger_id, ts, service, event_type, status, payload) "
            "VALUES ($1, now(), 'mras-vision','track','opened','{}'::jsonb)",
            str(uuid.uuid4()),
        )
    max_id = await pool.fetchval("SELECT max(id) FROM events")
    last_ts = datetime.now(timezone.utc) - timedelta(seconds=last_event_age_s)
    await pool.execute(
        "UPDATE projector_state SET cursor=$1, last_event_ts=$2, projector_ver='test-ver', updated_at=now() WHERE id=1",
        cursor, last_ts,
    )
    return max_id


async def test_status_reports_backlog_and_ok_health(projector_pool):
    max_id = await _seed(projector_pool, extra_events=5, cursor=0, last_event_age_s=2)
    async with projector_pool.acquire() as conn:
        st = await get_projector_status(conn, CFG)
    assert st["cursor"] == 0
    assert st["backlog"] == max_id  # max_event_id - cursor(0)
    assert st["projector_ver"] == "test-ver"
    assert 0 <= st["lag_seconds"] < 10
    assert st["health"] == "ok"


async def test_status_warn_when_lag_over_warn_threshold(projector_pool):
    max_id = await _seed(projector_pool, extra_events=3, cursor=1, last_event_age_s=30)
    async with projector_pool.acquire() as conn:
        st = await get_projector_status(conn, CFG)
    assert st["backlog"] == max_id - 1
    assert 10 <= st["lag_seconds"] < 60
    assert st["health"] == "warn"


async def test_status_crit_when_lag_over_crit_threshold(projector_pool):
    await _seed(projector_pool, extra_events=1, cursor=1, last_event_age_s=120)
    async with projector_pool.acquire() as conn:
        st = await get_projector_status(conn, CFG)
    assert st["lag_seconds"] >= 60
    assert st["health"] == "crit"
