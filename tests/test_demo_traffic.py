"""Demo-traffic generator (Globe Plan A, spec §6): sequence shape, payload
contract vs the projector handlers, timestamp stamping, org hard-scope guard.
Pure unit tests (AsyncMock DB) — no live services needed."""
import json
from unittest.mock import AsyncMock

import pytest

from scripts.demo_traffic import (DEMO_ORG_IDS, DEMO_UMBRELLA_ORG, SETTLE_GAP_S,
                                  build_sequence, emit_sequence, load_demo_orgs,
                                  run)

TARGET = {
    "system_id": "33333333-3333-4333-8333-333333333333",
    "location_id": "22222222-2222-4222-8222-222222222222",
    "system_name": "Entrance Wall A",
    "venue": "Mall of America",
    "camera_screen_id": "demo-cam-moa-1-1",
    "display_screen_id": "demo-disp-moa-1-1",
    "organization_id": "dea00000-0000-4000-8000-000000000002",  # Northline — a RETAILER, deliberately not the umbrella
}


def test_build_sequence_happy_path_order_and_scope():
    beats = build_sequence("trig-1", TARGET)
    keys = [(e, s) for _, e, s, _ in beats]
    # composition -> ad_run -> playback, in journal order (FK sibling lookups
    # by shared trigger_id require ascending-id existence)
    assert keys == [
        ("composition", "queued"), ("composition", "rendering"), ("ad_run", "planned"),
        ("composition", "rendered"),
        ("playback", "dispatched"), ("ad_run", "dispatched"),
        ("playback", "started"), ("ad_run", "playing"),
        ("playback", "ended"), ("ad_run", "completed"),
    ]
    for _, event_type, status, payload in beats:
        assert payload["demo_seed"] is True
        assert payload["trigger_id"] == "trig-1"
        assert payload["screen_kind"] in ("camera", "display")
    # composer scope convention: render lane = camera scope, playback lane = display
    by = {(e, s): p for _, e, s, p in beats}
    assert by[("composition", "queued")]["screen_kind"] == "camera"
    assert by[("composition", "queued")]["screen_id"] == "demo-cam-moa-1-1"
    assert by[("ad_run", "planned")]["screen_kind"] == "camera"
    assert by[("playback", "dispatched")]["screen_kind"] == "display"
    assert by[("playback", "dispatched")]["screen_id"] == "demo-disp-moa-1-1"
    assert by[("playback", "dispatched")]["ad_run_trigger_id"] == "trig-1"
    assert by[("ad_run", "completed")]["screen_kind"] == "display"
    # no phantom FK payloads: a non-existent ad_id would FK-violate in the
    # handler savepoint and silently drop the pulse
    for _, _, _, payload in beats:
        assert "ad_id" not in payload and "campaign_id" not in payload
        assert "target_subject_profile_id" not in payload


def test_build_sequence_respects_settle_window():
    beats = build_sequence("t", TARGET)
    # every status-advancing beat with a delay waits out the projector settle
    delays = [d for d, _, _, _ in beats if d > 0]
    assert delays, "sequence must pace itself"
    assert all(d >= SETTLE_GAP_S for d in delays)


def test_build_sequence_failure_path():
    beats = build_sequence("t", TARGET, fail=True)
    keys = [(e, s) for _, e, s, _ in beats]
    assert keys == [
        ("composition", "queued"), ("composition", "rendering"), ("ad_run", "planned"),
        ("composition", "failed"), ("ad_run", "failed"),
    ]
    by = {(e, s): p for _, e, s, p in beats}
    assert by[("composition", "failed")]["error_code"] == "DEMO_RENDER_FAIL"
    assert "playback" not in {e for e, _ in keys}


async def test_emit_sequence_stamps_scope_and_timestamps():
    pool = AsyncMock()
    sleep = AsyncMock()
    beats = build_sequence("trig-2", TARGET)
    await emit_sequence(pool, TARGET, "trig-2", beats, sleep=sleep)
    assert pool.execute.await_count == len(beats)
    for call in pool.execute.await_args_list:
        args = call.args
        # (sql, trigger_id, service, event_type, status, payload_json, org, loc, sys)
        assert "INSERT INTO events" in args[0]
        assert args[2] == "mras-composer"  # projector routing registry service key
        # stamped with the TARGET system's org — NEVER the umbrella. The
        # projector back-stamps org from the system row; insert-time value must
        # match so the back-stamp overwrites with identical values.
        assert args[6] == TARGET["organization_id"]
        assert args[6] != DEMO_UMBRELLA_ORG
        assert args[7] == TARGET["location_id"]
        assert args[8] == TARGET["system_id"]
        payload = json.loads(args[5])
        assert payload["demo_seed"] is True
    stamped = {(c.args[3], c.args[4]): json.loads(c.args[5])
               for c in pool.execute.await_args_list}
    assert "started_at" in stamped[("playback", "started")]
    assert "started_at" in stamped[("ad_run", "playing")]
    assert "ended_at" in stamped[("playback", "ended")]
    assert "duration_ms" in stamped[("playback", "ended")]
    assert "dispatched_at" in stamped[("playback", "dispatched")]
    assert "ended_at" in stamped[("ad_run", "completed")]
    # pacing went through the injected sleep, not real time
    assert sleep.await_count == sum(1 for d, *_ in beats if d > 0)


async def test_load_demo_orgs_hard_exits_when_umbrella_absent():
    # retailers alone are not enough — the umbrella is the seed sentinel
    pool = AsyncMock()
    pool.fetch = AsyncMock(return_value=[{"id": o} for o in DEMO_ORG_IDS[1:]])
    with pytest.raises(SystemExit):
        await load_demo_orgs(pool)
    assert pool.fetch.await_args.args[1] == DEMO_ORG_IDS


async def test_load_demo_orgs_returns_present_subset():
    pool = AsyncMock()
    present = [DEMO_ORG_IDS[0], DEMO_ORG_IDS[2]]  # umbrella + one retailer
    pool.fetch = AsyncMock(return_value=[{"id": o} for o in present])
    assert await load_demo_orgs(pool) == present
    # org-resolution query is set-scoped (load_targets' scoping is asserted separately)
    sql = pool.fetch.await_args.args[0]
    assert "ANY($1::uuid[])" in sql
    assert pool.fetch.await_args.args[1] == DEMO_ORG_IDS


async def test_run_drains_in_flight_tasks_without_mutation_error(monkeypatch):
    """Regression: run()'s post-duration drain loop iterates the live `tasks`
    set while each task's done-callback discards itself from that same set,
    raising 'RuntimeError: Set changed size during iteration'. Shrink the
    settle gap + jitter so many sequences overlap in a short --duration, and
    run several targets at a high rate to maximize task churn during drain."""
    monkeypatch.setattr("scripts.demo_traffic.SETTLE_GAP_S", 0.01)
    monkeypatch.setattr("random.uniform", lambda a, b: 0.005)

    targets = [
        {**TARGET, "system_id": f"sys-{i}", "location_id": f"loc-{i}",
         "system_name": f"System {i}", "venue": f"Venue {i}",
         "camera_screen_id": f"cam-{i}", "display_screen_id": f"disp-{i}",
         "organization_id": DEMO_ORG_IDS[1 + i % 4]}
        for i in range(6)
    ]
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value="dea00000-0000-4000-8000-000000000001")
    # two-fetch shape (org resolution, then targets) per run() call, repeated
    # for each of the outer loop's 5 invocations below.
    pool.fetch = AsyncMock(side_effect=[
        [{"id": o} for o in DEMO_ORG_IDS],       # load_demo_orgs
        [dict(t) for t in targets],              # load_targets
    ] * 5)
    monkeypatch.setattr("asyncpg.create_pool", AsyncMock(return_value=pool))

    for _ in range(5):
        await run(rate=6000.0, failure_pct=0.0, duration_s=0.3)
