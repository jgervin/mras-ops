"""Demo-traffic generator for the God View Globe (spec 2026-07-11 §6).

Appends minimal well-formed composition -> ad_run -> playback event sequences to
the append-only `events` journal for SEEDED venues only; the real projector
folds them into ad_runs/composition_runs/playbacks and the globe pulses. Never
projects, takes no advisory lock — the projector's single-writer discipline is
untouched.

Payload shapes are copied from the real emitters + the projector handlers
(/Users/jn/code/mras-composer/main.py, .../src/orchestrator/renderer.py,
/Users/jn/code/mras-ops/api/src/projector/handlers.py): every payload carries
screen_id + screen_kind (render lane = camera scope, playback lane = display
scope), timestamps are ISO strings, and nullable ad/subject FKs are OMITTED —
a payload naming a non-existent ad_id would FK-violate inside the projector's
per-event savepoint and silently drop the pulse.

Hard scope: targets come only from systems WHERE organization_id = the demo
org; hard-exits if the org is absent (cannot run post-teardown) and re-checks
each cycle. Every events row is stamped with organization_id + location_id +
system_id AT INSERT (plus payload.demo_seed=true) so rows still inside the
projector's settle window at Ctrl-C never escape the scoped teardown.

Usage:
    python -m scripts.demo_traffic [--rate RUNS_PER_MIN] [--failure-pct PCT]
                                   [--duration SECS]
Ctrl-C to stop. Doubles as projector load/soak tooling at high --rate.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import time
import uuid
from datetime import datetime, timezone

DEMO_UMBRELLA_ORG = "dea00000-0000-4000-8000-000000000001"  # Demo Retail Group
# The deterministic demo-org uuid family (seed v2): umbrella + 4 retailers.
# Targets are scoped to the PRESENT members of this set; events are stamped
# with each target system's own org (the projector back-stamps org from the
# system row — api/src/projector/scope.py — and must overwrite with identical
# values).
DEMO_ORG_IDS = [
    DEMO_UMBRELLA_ORG,
    "dea00000-0000-4000-8000-000000000002",  # Northline Apparel
    "dea00000-0000-4000-8000-000000000003",  # Vantage Motors
    "dea00000-0000-4000-8000-000000000004",  # Corebrew Coffee
    "dea00000-0000-4000-8000-000000000005",  # Meridian Screens
]
SERVICE = "mras-composer"  # must match the projector routing registry keys
# projector settle_ms=2000 + poll_ms=1000 => summaries trail inserts by ~2-3s;
# status beats are scheduled no tighter than this (expected lag, not a bug).
SETTLE_GAP_S = 3.0

_INSERT = (
    "INSERT INTO events (trigger_id, ts, service, event_type, status, payload, "
    "organization_id, location_id, system_id) "
    "VALUES ($1, now(), $2, $3, $4, $5::jsonb, $6, $7, $8)"
)

# emission-time timestamp stamping per (event_type, status)
_STAMPS = {
    ("composition", "rendering"): ("started_at",),
    ("composition", "rendered"): ("ended_at",),
    ("composition", "failed"): ("ended_at",),
    ("ad_run", "playing"): ("started_at",),
    ("ad_run", "completed"): ("ended_at",),
    ("ad_run", "failed"): ("ended_at",),
    ("playback", "dispatched"): ("dispatched_at",),
    ("playback", "started"): ("started_at",),
    ("playback", "ended"): ("ended_at",),
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_sequence(trigger_id: str, target: dict, fail: bool = False) -> list:
    """Ordered (delay_s, event_type, status, payload) beats for one demo run.

    composition -> ad_run -> playback IN THAT ORDER: the projector's FK-link
    lookups by shared trigger_id expect the sibling row to already exist
    (events fold in ascending id order). Timestamps are added at emit time."""
    cam = {"screen_id": target["camera_screen_id"], "screen_kind": "camera",
           "trigger_id": trigger_id, "demo_seed": True}
    disp = {"screen_id": target["display_screen_id"], "screen_kind": "display",
            "trigger_id": trigger_id, "demo_seed": True}

    def gap() -> float:  # jittered, never tighter than the settle window
        return SETTLE_GAP_S + random.uniform(0.0, 1.5)

    beats = [
        (0.0, "composition", "queued", dict(cam)),
        (gap(), "composition", "rendering", dict(cam)),
        (0.0, "ad_run", "planned", dict(cam)),
    ]
    if fail:
        beats += [
            (gap(), "composition", "failed",
             {**cam, "error_code": "DEMO_RENDER_FAIL",
              "error_message": "demo-traffic injected failure"}),
            (0.0, "ad_run", "failed", dict(cam)),
        ]
        return beats
    beats += [
        (gap(), "composition", "rendered", dict(cam)),
        (0.0, "playback", "dispatched",
         {**disp, "ad_run_trigger_id": trigger_id, "media_asset_ref": None}),
        (0.0, "ad_run", "dispatched", dict(disp)),
        (gap(), "playback", "started", dict(disp)),
        (0.0, "ad_run", "playing", dict(disp)),
        (SETTLE_GAP_S + random.uniform(2.0, 10.0), "playback", "ended", dict(disp)),
        (0.0, "ad_run", "completed", dict(disp)),
    ]
    return beats


async def emit_sequence(pool, target, trigger_id, beats,
                        sleep=asyncio.sleep, clock=time.monotonic) -> None:
    """Emit one sequence, pacing with `sleep`, stamping timestamps at emission.

    Events are stamped with the TARGET system's org (never the umbrella) so
    the projector's back-stamp overwrites with identical values."""
    started = None
    for delay_s, event_type, status, payload in beats:
        if delay_s > 0:
            await sleep(delay_s)
        payload = dict(payload)
        for field in _STAMPS.get((event_type, status), ()):
            payload[field] = now_iso()
        if (event_type, status) == ("playback", "started"):
            started = clock()
        if (event_type, status) == ("playback", "ended"):
            payload["duration_ms"] = int(((clock() - started) if started else 0) * 1000)
        await pool.execute(_INSERT, trigger_id, SERVICE, event_type, status,
                           json.dumps(payload), target["organization_id"],
                           target["location_id"], target["system_id"])
        print(f"[demo-traffic] {target['venue']} / {target['system_name']} "
              f"{event_type}/{status} trigger={trigger_id}")


async def load_demo_orgs(pool) -> list:
    """Hard-scope guard: return the PRESENT members of the demo-org family.
    The umbrella is the seed sentinel — hard-exit when it is absent (the
    generator cannot run post-teardown; teardown removes the whole family in
    one transaction)."""
    rows = await pool.fetch(
        "SELECT id FROM organizations "
        "WHERE id = ANY($1::uuid[]) AND metadata->>'demo_seed' = 'true'",
        DEMO_ORG_IDS)
    present = [str(r["id"]) for r in rows]
    if DEMO_UMBRELLA_ORG not in present:
        print("[demo-traffic] demo umbrella org absent — apply "
              "/Users/jn/code/mras-ops/db/seed/seed_demo_fleet.sql first. Exiting.")
        raise SystemExit(1)
    return present


async def load_targets(pool, org_ids) -> list[dict]:
    """One target per ACTIVE display of the demo-org SET (hard-scoped by the
    WHERE); each row carries its system's org for insert-time stamping."""
    rows = await pool.fetch(
        """
        SELECT s.id AS system_id, s.organization_id, s.location_id, s.name AS system_name,
               l.name AS venue, d.screen_id AS display_screen_id,
               (SELECT c.screen_id FROM cameras c
                 WHERE c.system_id = s.id AND c.status <> 'retired'
                 ORDER BY c.screen_id LIMIT 1) AS camera_screen_id
        FROM systems s
        JOIN locations l ON l.id = s.location_id
        JOIN displays d ON d.system_id = s.id AND d.status = 'active'
        WHERE s.organization_id = ANY($1::uuid[])
        """, org_ids)
    return [dict(r) for r in rows if r["camera_screen_id"] is not None]


async def run(rate: float, failure_pct: float, duration_s: float | None) -> None:
    import asyncpg  # local import so unit tests never need the driver

    pool = await asyncpg.create_pool(
        os.getenv("DATABASE_URL", "postgresql://mras:mras@localhost:5432/mras"))
    try:
        org_ids = await load_demo_orgs(pool)
        targets = await load_targets(pool, org_ids)
        if not targets:
            print("[demo-traffic] no seeded active displays — exiting")
            raise SystemExit(1)
        # weighted venue activity: stable per-venue weights, some venues run hot
        weights = [1 + (hash(t["venue"]) % 5) for t in targets]
        print(f"[demo-traffic] {len(targets)} display targets, "
              f"rate={rate}/min, failure={failure_pct}%. Ctrl-C to stop.")
        tasks: set = set()
        t0 = time.monotonic()
        while duration_s is None or time.monotonic() - t0 < duration_s:
            # hard-scope re-check: umbrella is the seed sentinel — teardown
            # removes the whole family in one transaction, so the umbrella's
            # disappearance is the set's disappearance.
            if await pool.fetchval(
                    "SELECT 1 FROM organizations WHERE id = $1",
                    DEMO_UMBRELLA_ORG) is None:
                print("[demo-traffic] demo umbrella org gone (teardown) — exiting")
                break
            target = random.choices(targets, weights=weights, k=1)[0]
            trigger_id = str(uuid.uuid4())
            fail = random.random() * 100.0 < failure_pct
            beats = build_sequence(trigger_id, target, fail=fail)
            task = asyncio.create_task(
                emit_sequence(pool, target, trigger_id, beats))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
            # jittered pacing around the fleet-wide rate
            await asyncio.sleep(random.uniform(0.5, 1.5) * 60.0 / rate)
        for task in list(tasks):  # snapshot: done-callbacks mutate `tasks` mid-drain
            await task
    finally:
        await pool.close()


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="God View demo-traffic generator")
    parser.add_argument("--rate", type=float, default=3.0,
                        help="run sequences per minute, fleet-wide (default 3)")
    parser.add_argument("--failure-pct", type=float, default=10.0,
                        help="percent of runs that take the failure path (default 10)")
    parser.add_argument("--duration", type=float, default=None,
                        help="stop after N seconds (default: run until Ctrl-C)")
    args = parser.parse_args(argv)
    try:
        asyncio.run(run(args.rate, args.failure_pct, args.duration))
    except KeyboardInterrupt:
        print("\n[demo-traffic] stopped")


if __name__ == "__main__":
    main()
