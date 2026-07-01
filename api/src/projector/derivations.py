"""PART B — viewer_exposures DERIVATION (projector join, not event-projection).

`viewer_exposures` has key `UNIQUE(ad_run_id, subject_observation_id)` (018). Its
two sides originate in different lanes — `ad_run_id` display-side (composer
`ad_run/*`), `subject_observation_id` camera-side (vision `detection/success`) —
so no single emitter can produce the row (review-architect §3, Critical #3). The
projector is the only component that holds both sides, so it DERIVES the table by
joining the summary tables it already owns.

Grain: one row per (ad_run, in-window co-scope observation), upserted on the 018
key — replay-safe. Derivation runs only when the playback window is CLOSED
(`started_at` AND `ended_at` present); an open window fabricates nothing.

Semantics chosen (documented in the report where the design under-specifies):
  * Co-scope   = `subject_observations.system_id == playbacks.system_id`
                 (MVP system-level grain, review-architect §3 rule 1).
  * Time-window= `observed_at BETWEEN playback.started_at AND playback.ended_at`.
  * role       = `target` when `observation.subject_profile_id ==
                 ad_run.target_subject_profile_id`, else `bystander`
                 (per the build directive — see report for the §3 trigger_id
                 divergence note).
  * identity_status derives from the observation's `observation_match` value.
  * targets carry `watched` (from the attention snapshot's attending flag);
    bystanders carry `watch_probability` (from `attending_fraction`).
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
        "SELECT id, target_subject_profile_id FROM ad_runs WHERE id=$1", pb["ad_run_id"]
    )
    if adr is None:
        return 0
    target_profile = adr["target_subject_profile_id"]

    observations = await conn.fetch(
        "SELECT id, observation_track_id, subject_profile_id, match_status, "
        "identity_confidence, attention_snapshot, mood_snapshot, demographic_snapshot "
        "FROM subject_observations "
        "WHERE system_id=$1 AND observed_at BETWEEN $2 AND $3",
        pb["system_id"], pb["started_at"], pb["ended_at"],
    )

    count = 0
    for o in observations:
        is_target = (
            target_profile is not None and o["subject_profile_id"] == target_profile
        )
        role = "target" if is_target else "bystander"
        identity_status = _IDENTITY_STATUS.get(o["match_status"], "unmatched")

        att = _snap(o["attention_snapshot"])
        mood = _snap(o["mood_snapshot"])
        demographic = _snap(o["demographic_snapshot"]) or None
        attending_fraction = att.get("attending_fraction")
        # targets: exact "watched" from the attending flag; bystanders: probability.
        watched = att.get("attending") if is_target else None
        watch_probability = None if is_target else attending_fraction

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
                watch_probability    = EXCLUDED.watch_probability,
                watched              = EXCLUDED.watched,
                gaze_duration_ms     = EXCLUDED.gaze_duration_ms,
                visible_duration_ms  = EXCLUDED.visible_duration_ms,
                attending_fraction   = EXCLUDED.attending_fraction,
                distance_estimate_m  = EXCLUDED.distance_estimate_m,
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
            attending_fraction,
            att.get("distance_estimate_m"),
            mood.get("mood_label"),
            mood.get("mood_confidence"),
            mood.get("expression_label"),
            mood.get("expression_confidence"),
            _jsonb(demographic),
        )
        count += 1
    return count
