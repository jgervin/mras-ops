"""PART B — viewer_exposures DERIVATION (projector join, not event-projection).

`viewer_exposures` has key `UNIQUE(ad_run_id, subject_observation_id)` (018). Its
two sides originate in different lanes — `ad_run_id` display-side (composer
`ad_run/*`), `subject_observation_id` camera-side (vision `detection/success`) —
so no single emitter can produce the row (review-architect §3, Critical #3). The
projector is the only component that holds both sides, so it DERIVES the table by
joining the summary tables it already owns.

Grain: one TARGET row (the causal triggering observation) + one row per in-window
co-scope BYSTANDER observation, upserted on the 018 key — replay-safe. Derivation
runs only when the playback window is CLOSED (`started_at` AND `ended_at` present);
an open window fabricates nothing.

Semantics chosen (documented in the report where the design under-specifies):
  * Target     = the subject_observation whose `trigger_id == ad_run.trigger_id`
                 (the exact causal link). Selected UNCONDITIONALLY of the playback
                 window: the triggering detection fires BEFORE the ad plays, so its
                 `observed_at` is OUTSIDE [started_at, ended_at] — a window-gated
                 join never sees it (review-architect FIX 1). Skipped defensively
                 when no observation carries that trigger_id.
  * Bystanders = in-window co-scope observations (`system_id == playback.system_id`
                 AND `observed_at BETWEEN started_at AND ended_at`), EXCLUDING the
                 target observation so it is not double-counted.
  * identity_status derives from the observation's `observation_match` value.
  * Target `watched` = computed from IN-WINDOW attention of the target SUBJECT (did
    they attend during playback) — NOT inferred from the pre-window triggering
    snapshot. For an ANONYMOUS target (subject_profile_id NULL) with no in-window
    re-observation there is no way to re-identify the subject, so `watched` is left
    NULL (documented choice — see report).
  * Bystanders carry `watch_probability` (from `attending_fraction`).
  * gaze / attention / mood / demographics come from the observation snapshots
    where present, else NULL.
"""
import json

# observation_match (013 detection enum)  ->  identity_status (010 enum)
_IDENTITY_STATUS = {
    "matched_known": "known",
    "matched_anonymous": "anonymous",
    "new_anonymous": "anonymous",
    "suppressed": "suppressed",
    "no_match": "unmatched",
    "ignored": "unmatched",
}


def _snap(value):
    """Return a snapshot jsonb column as a dict ({} when absent/other type)."""
    if value is None:
        return {}
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    return value if isinstance(value, dict) else {}


def _jsonb(value):
    return None if value is None else json.dumps(value)


async def derive_viewer_exposures_for_playback(conn, playback_id) -> int:
    """Derive viewer_exposures for one completed playback. Returns rows upserted.

    No-op (returns 0) unless the playback window is CLOSED (started_at AND ended_at
    present) and the playback resolved both an ad_run and a system scope — the
    window + co-scope anchors the join needs. Idempotent: re-running converges on
    the 018 UNIQUE(ad_run_id, subject_observation_id) key."""
    pb = await conn.fetchrow(
        "SELECT id, ad_run_id, organization_id, location_id, system_id, display_id, "
        "started_at, ended_at FROM playbacks WHERE id=$1",
        playback_id,
    )
    if pb is None:
        return 0
    if pb["started_at"] is None or pb["ended_at"] is None:
        return 0  # window not closed — defer, fabricate nothing
    if pb["ad_run_id"] is None or pb["system_id"] is None:
        return 0  # missing join anchor

    adr = await conn.fetchrow(
        "SELECT id, trigger_id, target_subject_profile_id FROM ad_runs WHERE id=$1", pb["ad_run_id"]
    )
    if adr is None:
        return 0

    _COLS = (
        "id, observation_track_id, subject_profile_id, match_status, "
        "identity_confidence, attention_snapshot, mood_snapshot, demographic_snapshot"
    )

    count = 0
    target_obs_id = None

    # --- TARGET: the causal triggering observation, by exact trigger_id link.
    # UNCONDITIONAL of the playback window — the triggering detection fires BEFORE
    # the ad plays, so its observed_at is outside [started_at, ended_at] (FIX 1).
    if adr["trigger_id"] is not None:
        target_obs = await conn.fetchrow(
            f"SELECT {_COLS} FROM subject_observations "
            "WHERE trigger_id=$1 ORDER BY observed_at, id LIMIT 1",
            adr["trigger_id"],
        )
        if target_obs is not None:
            target_obs_id = target_obs["id"]
            # watched: from IN-WINDOW attention of the target SUBJECT — not from the
            # pre-window triggering snapshot. NULL for an anonymous target (no
            # subject_profile_id -> cannot re-identify) with no in-window re-observation.
            watched = None
            if target_obs["subject_profile_id"] is not None:
                watched = await conn.fetchval(
                    "SELECT bool_or((attention_snapshot->>'attending')::boolean) "
                    "FROM subject_observations "
                    "WHERE system_id=$1 AND subject_profile_id=$2 "
                    "AND observed_at BETWEEN $3 AND $4",
                    pb["system_id"], target_obs["subject_profile_id"],
                    pb["started_at"], pb["ended_at"],
                )
            await _upsert_exposure(conn, pb, adr, target_obs,
                                   role="target", watched=watched, watch_probability=None)
            count += 1

    # --- BYSTANDERS: in-window co-scope observations, EXCLUDING the target (so it is
    # not double-counted). This half already worked; the exclusion is the only change.
    bystanders = await conn.fetch(
        f"SELECT {_COLS} FROM subject_observations "
        "WHERE system_id=$1 AND observed_at BETWEEN $2 AND $3 "
        "AND ($4::uuid IS NULL OR id <> $4)",
        pb["system_id"], pb["started_at"], pb["ended_at"], target_obs_id,
    )
    for o in bystanders:
        att = _snap(o["attention_snapshot"])
        await _upsert_exposure(conn, pb, adr, o, role="bystander",
                               watched=None, watch_probability=att.get("attending_fraction"))
        count += 1
    return count


async def _upsert_exposure(conn, pb, adr, o, *, role, watched, watch_probability) -> None:
    """Upsert one viewer_exposures row for observation ``o`` under the 018 key
    UNIQUE(ad_run_id, subject_observation_id). Measurement columns are sourced from
    the observation's own snapshots; ``watched`` / ``watch_probability`` are supplied
    by the caller per role. FIX 3: the measurement columns are COALESCE'd on conflict
    so a later NULL re-derive can't wipe a previously-recorded measurement."""
    identity_status = _IDENTITY_STATUS.get(o["match_status"], "unmatched")
    att = _snap(o["attention_snapshot"])
    mood = _snap(o["mood_snapshot"])
    demographic = _snap(o["demographic_snapshot"]) or None
    await conn.execute(
        """
        INSERT INTO viewer_exposures (
            ad_run_id, playback_id, organization_id, location_id, system_id, display_id,
            subject_profile_id, subject_observation_id, observation_track_id,
            role, identity_status, identity_confidence,
            watch_probability, watched,
            gaze_duration_ms, visible_duration_ms, attending_fraction, distance_estimate_m,
            mood_label, mood_confidence, expression_label, expression_confidence,
            demographic_snapshot
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23::jsonb)
        ON CONFLICT (ad_run_id, subject_observation_id) DO UPDATE SET
            playback_id          = EXCLUDED.playback_id,
            organization_id      = COALESCE(EXCLUDED.organization_id, viewer_exposures.organization_id),
            location_id          = COALESCE(EXCLUDED.location_id, viewer_exposures.location_id),
            system_id            = COALESCE(EXCLUDED.system_id, viewer_exposures.system_id),
            display_id           = COALESCE(EXCLUDED.display_id, viewer_exposures.display_id),
            subject_profile_id   = EXCLUDED.subject_profile_id,
            observation_track_id = EXCLUDED.observation_track_id,
            role                 = EXCLUDED.role,
            identity_status      = EXCLUDED.identity_status,
            identity_confidence  = EXCLUDED.identity_confidence,
            watch_probability    = COALESCE(EXCLUDED.watch_probability, viewer_exposures.watch_probability),
            watched              = COALESCE(EXCLUDED.watched, viewer_exposures.watched),
            gaze_duration_ms     = COALESCE(EXCLUDED.gaze_duration_ms, viewer_exposures.gaze_duration_ms),
            visible_duration_ms  = COALESCE(EXCLUDED.visible_duration_ms, viewer_exposures.visible_duration_ms),
            attending_fraction   = COALESCE(EXCLUDED.attending_fraction, viewer_exposures.attending_fraction),
            distance_estimate_m  = COALESCE(EXCLUDED.distance_estimate_m, viewer_exposures.distance_estimate_m),
            mood_label           = EXCLUDED.mood_label,
            mood_confidence      = EXCLUDED.mood_confidence,
            expression_label     = EXCLUDED.expression_label,
            expression_confidence= EXCLUDED.expression_confidence,
            demographic_snapshot = COALESCE(EXCLUDED.demographic_snapshot, viewer_exposures.demographic_snapshot)
        """,
        adr["id"],
        pb["id"],
        pb["organization_id"],
        pb["location_id"],
        pb["system_id"],
        pb["display_id"],
        o["subject_profile_id"],
        o["id"],
        o["observation_track_id"],
        role,
        identity_status,
        o["identity_confidence"],
        watch_probability,
        watched,
        att.get("gaze_duration_ms"),
        att.get("visible_duration_ms"),
        att.get("attending_fraction"),
        att.get("distance_estimate_m"),
        mood.get("mood_label"),
        mood.get("mood_confidence"),
        mood.get("expression_label"),
        mood.get("expression_confidence"),
        _jsonb(demographic),
    )
