"""Demo-traffic generator (Globe Plan A, spec §6): sequence shape, payload
contract vs the projector handlers, timestamp stamping, org hard-scope guard.
Pure unit tests (AsyncMock DB) — no live services needed."""
import json
from unittest.mock import AsyncMock

import pytest

from scripts.demo_traffic import (SETTLE_GAP_S, build_sequence, emit_sequence,
                                  load_demo_org)

TARGET = {
    "system_id": "33333333-3333-4333-8333-333333333333",
    "location_id": "22222222-2222-4222-8222-222222222222",
    "system_name": "Entrance Wall A",
    "venue": "Mall of America",
    "camera_screen_id": "demo-cam-moa-1-1",
    "display_screen_id": "demo-disp-moa-1-1",
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
    await emit_sequence(pool, "dea00000-0000-4000-8000-000000000001", TARGET,
                        "trig-2", beats, sleep=sleep)
    assert pool.execute.await_count == len(beats)
    for call in pool.execute.await_args_list:
        args = call.args
        # (sql, trigger_id, service, event_type, status, payload_json, org, loc, sys)
        assert "INSERT INTO events" in args[0]
        assert args[2] == "mras-composer"  # projector routing registry service key
        assert args[6] == "dea00000-0000-4000-8000-000000000001"
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


async def test_load_demo_org_hard_exits_when_absent():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=None)
    with pytest.raises(SystemExit):
        await load_demo_org(pool)
