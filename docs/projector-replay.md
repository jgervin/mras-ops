# Projector Replay Runbook — cursor reset for dev/demo databases

**Status:** Decision record + operational runbook. Resolves issue #39.
**Applies to:** the God View projector worker (`api/src/projector/`, compose service
`mras-ops-projector`). All file:line citations below are relative to the repo root and were
verified against the code at the time of writing.

---

## 1. Why replay exists

The projector is a forward-only folder. Each batch reads events strictly after the cursor
(`api/src/projector/fold.py:32`, `SELECT * FROM events WHERE id > $1 ORDER BY id ASC`), folds
them, and advances the singleton cursor in the same transaction
(`api/src/projector/fold.py:99-100`, `api/src/projector/cursor.py:15-24`). An event folded once
is **never re-processed** — including events that were folded (or skipped) by a *buggy or older
handler version*. Deploying a handler fix corrects the future, not the past.

**Concrete case (issue #39):** every `detection/success` event folded before the
`handle_detection` payload fix (PR #38) raised inside its savepoint and was routed to the
`projector.skip` audit log (`api/src/projector/fold.py:94-96`). No `subject_observations` row
was written for those detections. When the corresponding `playback/ended` events folded, the
`viewer_exposures` derivation ran (`api/src/projector/fold.py:88-90`) and found **no target
observation** — so those playbacks' exposures were derived with no target row, permanently.

**Replay heals this.** Events replay in ascending `id` order, and a detection has a lower `id`
than the `playback/ended` that depends on it — an ingestion-ordering assumption on the bigserial
`events.id` (the detection is ingested first), not a schema guarantee. On replay the fixed handler folds the
detection into `subject_observations` (`api/src/projector/handlers.py:70-127`), and when the
`playback/ended` event refolds, `derive_viewer_exposures_for_playback` re-runs and now resolves
the target (`api/src/projector/derivations.py:159-231`).

Because the `events` journal is append-only — payloads and timestamps are immutable, though the
typed scope columns are the projector's own mutable back-stamp (§2.5) — and the summary tables
are keyed upserts, the projection is (with the caveats in §2 and §4) **recomputable from the log**.

## 2. Safety model — what makes replay safe (verified in code)

Every claim below cites the line that enforces it.

### 2.1 Single writer — no concurrent folders

The worker holds a session-scoped Postgres advisory lock on a **dedicated** connection for its
whole lifetime (`api/src/projector/worker.py:5-9`, `api/src/projector/lock.py:10-12`). It never
folds without the lock (`api/src/projector/worker.py:92-96`), so two projector instances cannot
interleave a replay. Additionally, the fold reads the cursor `FOR UPDATE`
(`api/src/projector/cursor.py:10-12`), row-locking the singleton `projector_state` row for the
batch transaction.

### 2.2 Atomic batches, per-event savepoints

One batch = one transaction: cursor read, all upserts, back-stamps, and cursor advance commit or
roll back together (`api/src/projector/fold.py:44`, `fold.py:99-100`). Each event runs in its own
savepoint (`api/src/projector/fold.py:63`), so one bad event rolls back only its own writes; the
cursor still advances past it (poison events never wedge the pipeline,
`api/src/projector/fold.py:94-96`).

The cursor advance is guarded monotonic (`WHERE id = 1 AND cursor < $1`,
`api/src/projector/cursor.py:20`) — the fold itself can never move the cursor backwards. Replay
therefore requires the *manual* `UPDATE` in §3; nothing in the code path does it.

### 2.3 Idempotent upserts — ON CONFLICT keys per summary table

Every handler writes via `INSERT ... ON CONFLICT (<key>) DO UPDATE`
(`api/src/projector/handlers.py:1-15`) — one upsert per event, except `handle_identity_match`,
which fans out one keyed upsert per candidate (`api/src/projector/handlers.py:149-176`) — so
refolding the same event converges onto the same rows:

| Summary table | ON CONFLICT key | Citation |
|---|---|---|
| `observation_tracks` | `(camera_screen_id, camera_track_id)` | `api/src/projector/handlers.py:42` |
| `subject_observations` | `(event_id)` | `api/src/projector/handlers.py:86` |
| `identity_matches` | `(subject_observation_id, rank)` | `api/src/projector/handlers.py:156` |
| `personalization_decisions` | `(event_id)` | `api/src/projector/handlers.py:193` |
| `composition_runs` | `(trigger_id)` | `api/src/projector/handlers.py:254` |
| `ad_runs` | `(trigger_id)` | `api/src/projector/handlers.py:325` |
| `playbacks` | `(trigger_id, screen_id)` | `api/src/projector/handlers.py:410` |
| `viewer_exposures` | `(ad_run_id, subject_observation_id)` | `api/src/projector/derivations.py:285`, key frozen in `db/migrations/018_projector_keys.sql:32-34` |

Update clauses are convergence-safe: scope/FK columns are `COALESCE`'d so a NULL re-derive keeps
prior values (e.g. `api/src/projector/handlers.py:43-47`), counters use `GREATEST`
(`observation_count`, `api/src/projector/handlers.py:48`), consent booleans use `OR`
(`api/src/projector/handlers.py:263-266`). Status columns take `EXCLUDED.status`; since replay
folds in the original `id` order, the final status equals the original final status.

### 2.4 viewer_exposures never wipes recorded measurements

`_upsert_exposure` `COALESCE`s every measurement column on conflict — `watch_probability`,
`watched`, `gaze_duration_ms`, `visible_duration_ms`, `attending_fraction`,
`distance_estimate_m`, `demographic_snapshot` (`api/src/projector/derivations.py:296-301,306`) —
so a re-derivation that produces NULL for a measurement cannot wipe a previously-recorded value.
The derivation self-guards: it is a no-op unless the playback window is closed
(`api/src/projector/derivations.py:140-141`) and both join anchors resolved
(`derivations.py:142-143`). The gaze join reads only the `events` journal
(`api/src/projector/derivations.py:87-95`); event payloads are immutable, so on a stable device
registry it recomputes identically on replay. **Exception:** the join filters on
`events.system_id` (`derivations.py:91`), which is the projector's own mutable back-stamp
(`fold.py:110`) — see §2.6(3) for how registry drift breaks this and why COALESCE does not save
you there.

### 2.5 Scope back-stamping is idempotent — with one registry caveat

The fold back-stamps resolved scope uuids onto the source `events` row
(`api/src/projector/fold.py:104-122`). `subject_profile_id` / `ad_run_id` are `COALESCE`'d
(`fold.py:112-113`) — never wiped. The five device-scope columns, however, are set
**unconditionally** (`fold.py:110-111`): re-stamping the same resolution is a no-op, but replay
re-resolves scope against the **current** device registry. See §2.6(3).

### 2.6 What is NOT idempotent on replay (stated plainly)

1. **`audit_logs` rows duplicate.** `_write_skip` is a plain `INSERT` with no uniqueness
   (`api/src/projector/fold.py:136-142`; table has no unique key beyond its uuid PK,
   `db/migrations/016_events_audit.sql:21-30`). Any event that *still* skips or resolve-misses
   on replay writes a **second** `projector.skip` / `projector.resolve_miss` row. Note the
   original skip rows from the buggy pass also remain — the audit log is append-only history,
   which is arguably correct, but counts of skip rows are per-fold-attempt, not per-event.
2. **`unresolved_devices.seen_count` inflates.** The row itself is idempotent
   (`ON CONFLICT (screen_id, kind)`, `api/src/projector/scope.py:91-97`;
   `db/migrations/020_device_registry.sql:27`) — no duplicate rows — but the counter is not
   replay-stable. `_record_unresolved` fires only on a resolver cache miss
   (`api/src/projector/scope.py:65-68`); the worker builds a fresh `ScopeResolver` per batch
   (`api/src/projector/worker.py:60`) with a 60 s TTL cache (`api/src/projector/scope.py:50-71`),
   so each miss bumps `seen_count + 1` and touches `last_seen_at`
   (`api/src/projector/scope.py:93`) roughly **once per unresolved device per batch** (batch
   size 500 default), not once per event. `seen_count` was therefore never a raw sighting
   count — it counts cache-missed resolutions — and a replay inflates it by roughly one per
   batch that touches that device.
3. **Events scope back-stamp reflects the registry at replay time.** A device retired or
   deleted since the original fold resolves to the NULL scope
   (`status <> 'retired'` filters, `api/src/projector/scope.py:33,39`; NULL bundle,
   `scope.py:64-68`), and the unconditional back-stamp (`api/src/projector/fold.py:110-111`)
   will **overwrite the events row's device-scope columns to NULL**. Summary tables keep their
   old scope (COALESCE, §2.3); the raw events rows do not.

   This also corrupts recomputed *measurements*, not just stamps: the gaze join filters on the
   back-stamped `events.system_id` (`api/src/projector/derivations.py:91`). Replaying across a
   camera retirement re-stamps that camera's gaze events to NULL `system_id` *before* the
   dependent `playback/ended` refolds; the join then finds 0 rows and — for a target with a
   `camera_track_id` — recomputes `watched = FALSE` (`derivations.py:208-211`), a **non-NULL**
   value, so `COALESCE(EXCLUDED.watched, ...)` (`derivations.py:297`) **overwrites a previously
   recorded `watched = TRUE`**. Bystander `watch_probability` can regress the same way via the
   attention-snapshot fallback (`derivations.py:249-254`). COALESCE protects against NULL
   re-derives, **not** against recomputed non-NULL values. The "do not replay across device
   retirements" advice stands for this stronger reason, not just cosmetic stamp loss.
4. **Replay upserts; it never deletes.** A row written by an older buggy handler under a key the
   fixed handler no longer produces is left behind, stale. Replay is per-key convergence, not
   reconciliation. (No known instance today; noted so nobody assumes otherwise.)
5. `updated_at` / heartbeat columns churn (`api/src/projector/handlers.py:51,347`,
   `api/src/projector/cursor.py:19`). Cosmetic.

## 3. Procedure (dev/demo DB only)

Prereq: the fixed projector image is built/deployed (replaying under the old handler just
re-skips the same events and duplicates skip audits, §2.6.1).

1. **Stop the projector** (drains the batch and releases the advisory lock on SIGTERM,
   `docker-compose.yml:136-143`, `api/src/projector/worker.py:101-104`):

   ```sh
   docker compose stop mras-ops-projector
   ```

2. **Record the pre-replay baseline** (optional but recommended):

   ```sql
   SELECT cursor FROM projector_state WHERE id = 1;
   SELECT (SELECT count(*) FROM subject_observations)  AS obs,
          (SELECT count(*) FROM viewer_exposures)      AS exposures,
          (SELECT count(*) FROM viewer_exposures WHERE role = 'target') AS targets,
          (SELECT count(*) FROM audit_logs WHERE action LIKE 'projector.%') AS skips;
   ```

3. **Reset the cursor.** `0` replays everything; a chosen `events.id` replays everything after
   it (see §4 on bounded replay):

   ```sql
   UPDATE projector_state SET cursor = 0, last_event_ts = NULL WHERE id = 1;
   ```

   (`projector_state` is the id=1 singleton from `db/migrations/019_projector_state.sql:4-11`.)

4. **Restart and watch it catch up.** The worker drains full batches back-to-back
   (batch 500, poll 1000 ms defaults — `api/src/projector/config.py:38-44`,
   `api/src/projector/worker.py:63-76`):

   ```sh
   docker compose start mras-ops-projector
   ```

   Watch `cursor` converge to `max(events.id)` — via SQL:

   ```sql
   SELECT ps.cursor,
          (SELECT COALESCE(max(id), 0) FROM events) AS max_event_id,
          (SELECT COALESCE(max(id), 0) FROM events) - ps.cursor AS backlog
   FROM projector_state ps WHERE ps.id = 1;
   ```

   or via the ops API's `GET /projector/status` (`api/src/main.py:207-212`,
   `api/src/projector/status.py:23-51`), which reports `cursor`, `backlog`, `lag_seconds`, and
   `health`. Expect `health: crit` while the backlog drains — that is the lag metric working,
   not a failure. Done when `backlog` reaches 0 (the settle window holds back only the newest
   ~2 s of events, `api/src/projector/fold.py:53-61`).

5. **Spot-check expectations.**

   Previously-skipped detections now have observation rows (expect **0**):

   ```sql
   SELECT count(*)
   FROM audit_logs al
   LEFT JOIN subject_observations so ON so.event_id = al.entity_id::bigint
   WHERE al.action = 'projector.skip'
     AND al.before->>'event_type' = 'detection'
     AND so.id IS NULL;
   ```

   Target exposures appeared for the affected playbacks (compare against the §3.2 baseline —
   `targets` should have grown, `obs`/`exposures` grown or equal, and no summary-table count
   should have shrunk):

   ```sql
   SELECT count(*) FROM viewer_exposures WHERE role = 'target';
   ```

   Expected audit noise: `audit_logs` `projector.%` rows grow (§2.6.1) and
   `unresolved_devices.seen_count` inflates (§2.6.2). Both are cosmetic in dev/demo.

## 4. Caveats and when NOT to replay

- **Cost at scale.** A full replay refolds every event; the exposure derivation additionally
  re-runs a per-observation gaze join over the events journal for every closed playback
  (`api/src/projector/derivations.py:87-95`, `fold.py:88-90`), and the back-stamp UPDATEs every
  replayed `events` row (`fold.py:104-122`) — dead-tuple/WAL churn on the largest table. Trivial
  on a demo DB (thousands of events, seconds); on a large journal this is minutes-to-hours of
  writer-lock time during which lag alarms fire.
- **Do NOT replay a production DB once real billing/measurement data exists.** The audit trail
  duplicates (§2.6.1), diagnostic counters rewrite (§2.6.2), event scope stamps re-resolve
  against the current registry (§2.6.3), and any downstream consumer that assumed summary rows
  were settled will observe them mutating. Accept the gap or build a bounded backfill first.
- **Bounded replay today:** step §3.3 already accepts any starting id — set `cursor` to the id
  just below the first affected event (e.g. the smallest `entity_id::bigint` among
  `projector.skip` rows) instead of `0` to shrink the blast radius.
- **Future work (not built, deliberately):** an automated bounded backfill — replay
  `[from_id, to_id]` without rewinding the live cursor, or a per-trigger re-derivation command.
  File a new issue if production data ever makes this necessary; do not extend this runbook
  into automation ad hoc.

## 5. Decision record

**Issue #39** ("Projector: no backfill for events folded before the handle_detection fix") is
resolved as: **documented replay runbook, no backfill automation** — this document. Owner-approved
conservative choice, 2026-07-04. Rationale: only dev/demo databases exist today, the handlers
were verified replay-idempotent (§2), and a full cursor-reset replay is a one-line `UPDATE`
(§3) — automation would add code (and failure modes) to guard data that does not yet exist.
