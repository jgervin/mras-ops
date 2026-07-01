"""T7 — Projection handlers: one idempotent UPSERT per summary table.

Each handler runs INSIDE the fold's per-event savepoint (never opens its own
transaction) and issues a single ``INSERT ... ON CONFLICT (<migration key>) DO
UPDATE ...``. The ON CONFLICT target is the idempotency key frozen by migration
018 / 021 / the pre-existing 013+015 constraints, so a replay (fold again from an
earlier cursor) converges onto the same rows instead of duplicating them.

Signature: ``async def handle_x(conn, env: EventEnvelope, scope: Scope) -> None``.
``scope`` is the ScopeResolver result the fold resolved once from
(screen_id, screen_kind); the fold also back-stamps it onto the events row.

Enum columns use ONLY real 010_enums.sql values — a bad enum raises inside the
savepoint and the fold routes the event to the ``projector.skip`` audit log.
"""
import json


class ResolveMiss(Exception):
    """A REQUIRED PARENT ROW the handler needs is absent (upstream data-completeness
    gap, not a bad event). The fold audits this as ``projector.resolve_miss`` — a
    distinct signal from ``projector.skip`` (poison/enum/malformed)."""


def _jsonb(value):
    """Encode a Python value for a jsonb bind param; None -> SQL NULL (not JSON null)."""
    return None if value is None else json.dumps(value)


# --------------------------------------------------------------------------- #
# observation_tracks  <-  track/opened, track/closed
# ON CONFLICT (camera_screen_id, camera_track_id)  [018]
# --------------------------------------------------------------------------- #
async def handle_track(conn, env, scope):
    await conn.execute(
        """
        INSERT INTO observation_tracks (
            camera_screen_id, camera_track_id, system_id, camera_id,
            subject_profile_id, started_at, ended_at, observation_count,
            max_identity_confidence, track_confidence
        ) VALUES ($1,$2,$3,$4,$5,$6::text::timestamptz,$7::text::timestamptz,$8,$9,$10)
        ON CONFLICT (camera_screen_id, camera_track_id) DO UPDATE SET
            system_id               = COALESCE(EXCLUDED.system_id, observation_tracks.system_id),
            camera_id               = COALESCE(EXCLUDED.camera_id, observation_tracks.camera_id),
            subject_profile_id      = COALESCE(EXCLUDED.subject_profile_id, observation_tracks.subject_profile_id),
            started_at              = COALESCE(EXCLUDED.started_at, observation_tracks.started_at),
            ended_at                = COALESCE(EXCLUDED.ended_at, observation_tracks.ended_at),
            observation_count       = GREATEST(EXCLUDED.observation_count, observation_tracks.observation_count),
            max_identity_confidence = COALESCE(EXCLUDED.max_identity_confidence, observation_tracks.max_identity_confidence),
            track_confidence        = COALESCE(EXCLUDED.track_confidence, observation_tracks.track_confidence),
            updated_at              = now()
        """,
        env.screen_id,
        env.payload_get("camera_track_id"),
        scope.system_id,
        scope.camera_id,
        env.payload_get("subject_profile_id"),
        env.payload_get("started_at"),
        env.payload_get("ended_at"),
        env.payload_get("observation_count", 0),
        env.payload_get("max_identity_confidence"),
        env.payload_get("track_confidence"),
    )


# --------------------------------------------------------------------------- #
# subject_observations  <-  detection/success
# ON CONFLICT (event_id)  [013]
# --------------------------------------------------------------------------- #
async def handle_detection(conn, env, scope):
    track_id = await conn.fetchval(
        "SELECT id FROM observation_tracks WHERE camera_screen_id=$1 AND camera_track_id=$2",
        env.screen_id,
        env.payload_get("camera_track_id"),
    )
    subject_profile_id = env.payload_get("uuid")  # matched subject_profile id, per contract
    await conn.execute(
        """
        INSERT INTO subject_observations (
            event_id, trigger_id, organization_id, location_id, system_id, camera_id,
            observation_track_id, observed_at, detection_type, subject_profile_id,
            camera_track_id, identity_confidence, match_status,
            bounding_box, face_quality_score, demographic_snapshot, mood_snapshot, attention_snapshot
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8::text::timestamptz,$9,$10,$11,$12,$13,
                  $14::jsonb,$15,$16::jsonb,$17::jsonb,$18::jsonb)
        ON CONFLICT (event_id) DO UPDATE SET
            trigger_id           = EXCLUDED.trigger_id,
            organization_id      = COALESCE(EXCLUDED.organization_id, subject_observations.organization_id),
            location_id          = COALESCE(EXCLUDED.location_id, subject_observations.location_id),
            system_id            = COALESCE(EXCLUDED.system_id, subject_observations.system_id),
            camera_id            = COALESCE(EXCLUDED.camera_id, subject_observations.camera_id),
            observation_track_id = COALESCE(EXCLUDED.observation_track_id, subject_observations.observation_track_id),
            observed_at          = EXCLUDED.observed_at,
            detection_type       = EXCLUDED.detection_type,
            subject_profile_id   = COALESCE(EXCLUDED.subject_profile_id, subject_observations.subject_profile_id),
            camera_track_id      = COALESCE(EXCLUDED.camera_track_id, subject_observations.camera_track_id),
            identity_confidence  = COALESCE(EXCLUDED.identity_confidence, subject_observations.identity_confidence),
            match_status         = EXCLUDED.match_status,
            bounding_box         = COALESCE(EXCLUDED.bounding_box, subject_observations.bounding_box),
            face_quality_score   = COALESCE(EXCLUDED.face_quality_score, subject_observations.face_quality_score),
            demographic_snapshot = COALESCE(EXCLUDED.demographic_snapshot, subject_observations.demographic_snapshot),
            mood_snapshot        = COALESCE(EXCLUDED.mood_snapshot, subject_observations.mood_snapshot),
            attention_snapshot   = COALESCE(EXCLUDED.attention_snapshot, subject_observations.attention_snapshot)
        """,
        env.id,
        env.trigger_id,
        scope.organization_id,
        scope.location_id,
        scope.system_id,
        scope.camera_id,
        track_id,
        env.payload_get("observed_at"),
        env.payload_get("detection_type"),
        subject_profile_id,
        env.payload_get("camera_track_id"),
        env.payload_get("confidence"),
        env.payload_get("match_status", "no_match"),
        _jsonb(env.payload_get("bounding_box")),
        env.payload_get("face_quality_score"),
        _jsonb(env.payload_get("demographic_snapshot")),
        _jsonb(env.payload_get("mood_snapshot")),
        _jsonb(env.payload_get("attention_snapshot")),
    )
    # FIX 2: hand the matched profile back so the fold can back-stamp events.subject_profile_id.
    return {"subject_profile_id": subject_profile_id}


# --------------------------------------------------------------------------- #
# identity_matches  <-  identity_match/candidates  (fan-out: one row per rank)
# ON CONFLICT (subject_observation_id, rank)  [018]
# --------------------------------------------------------------------------- #
async def handle_identity_match(conn, env, scope):
    det_event_id = env.payload_get("detection_event_id")
    if det_event_id is not None:
        obs_id = await conn.fetchval(
            "SELECT id FROM subject_observations WHERE event_id=$1", det_event_id
        )
    else:
        obs_id = await conn.fetchval(
            "SELECT id FROM subject_observations WHERE trigger_id=$1 ORDER BY id LIMIT 1",
            env.trigger_id,
        )
    if obs_id is None:
        raise ResolveMiss(
            f"identity_match: no subject_observation for detection_event_id={det_event_id} "
            f"trigger_id={env.trigger_id}"
        )
    for cand in env.payload_get("candidates", []) or []:
        await conn.execute(
            """
            INSERT INTO identity_matches (
                subject_observation_id, rank, candidate_subject_profile_id, candidate_embedding_id,
                match_status, confidence, threshold, qdrant_score, model_name, model_version
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (subject_observation_id, rank) DO UPDATE SET
                candidate_subject_profile_id = EXCLUDED.candidate_subject_profile_id,
                candidate_embedding_id       = EXCLUDED.candidate_embedding_id,
                match_status                 = EXCLUDED.match_status,
                confidence                   = EXCLUDED.confidence,
                threshold                    = EXCLUDED.threshold,
                qdrant_score                 = EXCLUDED.qdrant_score,
                model_name                   = EXCLUDED.model_name,
                model_version                = EXCLUDED.model_version
            """,
            obs_id,
            cand.get("rank"),
            cand.get("candidate_subject_profile_id"),
            cand.get("candidate_embedding_id"),
            cand.get("match_status", "no_match"),
            cand.get("confidence"),
            cand.get("threshold"),
            cand.get("qdrant_score"),
            cand.get("model_name"),
            cand.get("model_version"),
        )


# --------------------------------------------------------------------------- #
# personalization_decisions  <-  decision/made
# ON CONFLICT (event_id)  [018]
# --------------------------------------------------------------------------- #
async def handle_decision(conn, env, scope):
    await conn.execute(
        """
        INSERT INTO personalization_decisions (
            event_id, trigger_id, organization_id, location_id, system_id,
            campaign_id, selected_ad_id, selected_creative_id, decision_type,
            target_subject_profile_id, target_observation_id,
            identity_confidence, demographic_confidence, decision_confidence,
            decision_factors, prompt_used, model_run_id
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb,$16,$17)
        ON CONFLICT (event_id) DO UPDATE SET
            trigger_id                = EXCLUDED.trigger_id,
            organization_id           = COALESCE(EXCLUDED.organization_id, personalization_decisions.organization_id),
            location_id               = COALESCE(EXCLUDED.location_id, personalization_decisions.location_id),
            system_id                 = COALESCE(EXCLUDED.system_id, personalization_decisions.system_id),
            campaign_id               = EXCLUDED.campaign_id,
            selected_ad_id            = EXCLUDED.selected_ad_id,
            selected_creative_id      = EXCLUDED.selected_creative_id,
            decision_type             = EXCLUDED.decision_type,
            target_subject_profile_id = EXCLUDED.target_subject_profile_id,
            target_observation_id     = EXCLUDED.target_observation_id,
            identity_confidence       = EXCLUDED.identity_confidence,
            demographic_confidence    = EXCLUDED.demographic_confidence,
            decision_confidence       = EXCLUDED.decision_confidence,
            decision_factors          = EXCLUDED.decision_factors,
            prompt_used               = EXCLUDED.prompt_used,
            model_run_id              = EXCLUDED.model_run_id
        """,
        env.id,
        env.trigger_id,
        scope.organization_id,
        scope.location_id,
        scope.system_id,
        env.payload_get("campaign_id"),
        env.payload_get("selected_ad_id"),
        env.payload_get("selected_creative_id"),
        env.payload_get("decision_type"),
        env.payload_get("target_subject_profile_id"),
        env.payload_get("target_observation_id"),
        env.payload_get("identity_confidence"),
        env.payload_get("demographic_confidence"),
        env.payload_get("decision_confidence"),
        _jsonb(env.payload_get("decision_factors", {})),
        env.payload_get("prompt_used"),
        env.payload_get("model_run_id"),
    )


# --------------------------------------------------------------------------- #
# composition_runs  <-  composition/{queued,rendering,rendered,failed}
# ON CONFLICT (trigger_id)  [018].  status -> composition_status enum.
# --------------------------------------------------------------------------- #
async def handle_composition(conn, env, scope):
    # FK-link (Part A): resolve the sibling decision by shared trigger_id. Events fold
    # in ascending id order, so the decision/made row already exists here. N-variant
    # decisions share a trigger_id — LIMIT 1 takes a REPRESENTATIVE link (acceptable
    # for v1; a variant-exact link would need the decision's event_id on the wire).
    pd_id = env.payload_get("personalization_decision_id")
    if pd_id is None:
        pd_id = await conn.fetchval(
            "SELECT id FROM personalization_decisions WHERE trigger_id=$1 ORDER BY id LIMIT 1",
            env.trigger_id,
        )
    await conn.execute(
        """
        INSERT INTO composition_runs (
            trigger_id, organization_id, location_id, system_id,
            personalization_decision_id, ad_id, component_id, render_mode, status,
            used_spoken_name, used_visible_name, used_likeness, used_voice_clone,
            render_progress, error_code, error_message, started_at, ended_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,COALESCE($8::render_mode,'prebuilt'),$9,$10,$11,$12,$13,$14,$15,$16,$17::text::timestamptz,$18::text::timestamptz)
        ON CONFLICT (trigger_id) DO UPDATE SET
            organization_id             = COALESCE(EXCLUDED.organization_id, composition_runs.organization_id),
            location_id                 = COALESCE(EXCLUDED.location_id, composition_runs.location_id),
            system_id                   = COALESCE(EXCLUDED.system_id, composition_runs.system_id),
            personalization_decision_id = COALESCE(EXCLUDED.personalization_decision_id, composition_runs.personalization_decision_id),
            ad_id                       = COALESCE(EXCLUDED.ad_id, composition_runs.ad_id),
            component_id                = COALESCE(EXCLUDED.component_id, composition_runs.component_id),
            render_mode                 = COALESCE($8::render_mode, composition_runs.render_mode),
            status                      = EXCLUDED.status,
            used_spoken_name            = composition_runs.used_spoken_name OR EXCLUDED.used_spoken_name,
            used_visible_name           = composition_runs.used_visible_name OR EXCLUDED.used_visible_name,
            used_likeness               = composition_runs.used_likeness OR EXCLUDED.used_likeness,
            used_voice_clone            = composition_runs.used_voice_clone OR EXCLUDED.used_voice_clone,
            render_progress             = COALESCE(EXCLUDED.render_progress, composition_runs.render_progress),
            error_code                  = COALESCE(EXCLUDED.error_code, composition_runs.error_code),
            error_message               = COALESCE(EXCLUDED.error_message, composition_runs.error_message),
            started_at                  = COALESCE(EXCLUDED.started_at, composition_runs.started_at),
            ended_at                    = COALESCE(EXCLUDED.ended_at, composition_runs.ended_at)
        """,
        env.trigger_id,
        scope.organization_id,
        scope.location_id,
        scope.system_id,
        pd_id,
        env.payload_get("ad_id"),
        env.payload_get("component_id"),
        env.payload_get("render_mode"),  # NULL when omitted -> COALESCE keeps prior value (no clobber)
        env.status,
        bool(env.payload_get("used_spoken_name", False)),
        bool(env.payload_get("used_visible_name", False)),
        bool(env.payload_get("used_likeness", False)),
        bool(env.payload_get("used_voice_clone", False)),
        env.payload_get("render_progress"),
        env.payload_get("error_code"),
        env.payload_get("error_message"),
        env.payload_get("started_at"),
        env.payload_get("ended_at"),
    )


# --------------------------------------------------------------------------- #
# ad_runs  <-  ad_run/{planned,dispatched,playing,completed,failed}
# ON CONFLICT (trigger_id)  [015].  status -> ad_run_status enum.
# --------------------------------------------------------------------------- #
async def handle_ad_run(conn, env, scope):
    # FK-link (Part A): resolve the sibling composition + decision by shared trigger_id.
    # composition_runs is UNIQUE(trigger_id) — exact link. personalization_decisions is
    # keyed per event, so N-variant decisions share the trigger_id — LIMIT 1 is the
    # representative link (same v1 caveat as handle_composition).
    comp_run_id = env.payload_get("composition_run_id")
    if comp_run_id is None:
        comp_run_id = await conn.fetchval(
            "SELECT id FROM composition_runs WHERE trigger_id=$1", env.trigger_id
        )
    pd_id = env.payload_get("personalization_decision_id")
    if pd_id is None:
        pd_id = await conn.fetchval(
            "SELECT id FROM personalization_decisions WHERE trigger_id=$1 ORDER BY id LIMIT 1",
            env.trigger_id,
        )
    ad_run_id = await conn.fetchval(
        """
        INSERT INTO ad_runs (
            trigger_id, organization_id, location_id, system_id, display_id,
            campaign_id, ad_id, personalization_decision_id, composition_run_id,
            target_subject_profile_id, personalization_type,
            used_spoken_name, used_visible_name, used_likeness, used_voice_clone,
            target_watch_probability, estimated_total_viewers,
            estimated_identified_viewers, estimated_anonymous_viewers,
            status, started_at, ended_at
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,COALESCE($11::personalization_type,'none'),$12,$13,$14,$15,$16,$17,$18,$19,$20,$21::text::timestamptz,$22::text::timestamptz)
        ON CONFLICT (trigger_id) DO UPDATE SET
            organization_id             = COALESCE(EXCLUDED.organization_id, ad_runs.organization_id),
            location_id                 = COALESCE(EXCLUDED.location_id, ad_runs.location_id),
            system_id                   = COALESCE(EXCLUDED.system_id, ad_runs.system_id),
            display_id                  = COALESCE(EXCLUDED.display_id, ad_runs.display_id),
            campaign_id                 = COALESCE(EXCLUDED.campaign_id, ad_runs.campaign_id),
            ad_id                       = COALESCE(EXCLUDED.ad_id, ad_runs.ad_id),
            personalization_decision_id = COALESCE(EXCLUDED.personalization_decision_id, ad_runs.personalization_decision_id),
            composition_run_id          = COALESCE(EXCLUDED.composition_run_id, ad_runs.composition_run_id),
            target_subject_profile_id   = COALESCE(EXCLUDED.target_subject_profile_id, ad_runs.target_subject_profile_id),
            personalization_type        = COALESCE($11::personalization_type, ad_runs.personalization_type),
            used_spoken_name            = ad_runs.used_spoken_name OR EXCLUDED.used_spoken_name,
            used_visible_name           = ad_runs.used_visible_name OR EXCLUDED.used_visible_name,
            used_likeness               = ad_runs.used_likeness OR EXCLUDED.used_likeness,
            used_voice_clone            = ad_runs.used_voice_clone OR EXCLUDED.used_voice_clone,
            target_watch_probability    = COALESCE(EXCLUDED.target_watch_probability, ad_runs.target_watch_probability),
            estimated_total_viewers     = COALESCE(EXCLUDED.estimated_total_viewers, ad_runs.estimated_total_viewers),
            estimated_identified_viewers= COALESCE(EXCLUDED.estimated_identified_viewers, ad_runs.estimated_identified_viewers),
            estimated_anonymous_viewers = COALESCE(EXCLUDED.estimated_anonymous_viewers, ad_runs.estimated_anonymous_viewers),
            status                      = EXCLUDED.status,
            started_at                  = COALESCE(EXCLUDED.started_at, ad_runs.started_at),
            ended_at                    = COALESCE(EXCLUDED.ended_at, ad_runs.ended_at),
            updated_at                  = now()
        RETURNING id
        """,
        env.trigger_id,
        scope.organization_id,
        scope.location_id,
        scope.system_id,
        scope.display_id,
        env.payload_get("campaign_id"),
        env.payload_get("ad_id"),
        pd_id,
        comp_run_id,
        env.payload_get("target_subject_profile_id"),
        env.payload_get("personalization_type"),  # NULL when omitted -> COALESCE keeps prior value
        bool(env.payload_get("used_spoken_name", False)),
        bool(env.payload_get("used_visible_name", False)),
        bool(env.payload_get("used_likeness", False)),
        bool(env.payload_get("used_voice_clone", False)),
        env.payload_get("target_watch_probability"),
        env.payload_get("estimated_total_viewers"),
        env.payload_get("estimated_identified_viewers"),
        env.payload_get("estimated_anonymous_viewers"),
        env.status,
        env.payload_get("started_at"),
        env.payload_get("ended_at"),
    )
    # FIX 2: hand the resolved ad_run id back so the fold can back-stamp events.ad_run_id.
    return {"ad_run_id": ad_run_id}


# --------------------------------------------------------------------------- #
# playbacks  <-  playback/{dispatched,started,ended}
# ON CONFLICT (trigger_id, screen_id)  [021 rekey].  status -> playback_status.
# --------------------------------------------------------------------------- #
async def handle_playback(conn, env, scope):
    # FK-link (Part A): resolve ad_run by SHARED trigger_id (the playback and its ad_run
    # share the pipeline trigger). Honor an explicit ad_run_trigger_id when the relay
    # stamps one; otherwise fall back to this playback's own trigger_id (previously left
    # NULL). ad_runs is UNIQUE(trigger_id) — exact link.
    lookup_trigger = env.payload_get("ad_run_trigger_id") or env.trigger_id
    ad_run_id = None
    if lookup_trigger is not None:
        ad_run_id = await conn.fetchval(
            "SELECT id FROM ad_runs WHERE trigger_id=$1", lookup_trigger
        )
    # FK-link (Part A): resolve media_asset by ref -> media_assets.storage_url
    # (contract 01: media_asset_ref is a "video filename/url"). NULL when no match.
    media_asset_id = None
    media_asset_ref = env.payload_get("media_asset_ref")
    if media_asset_ref is not None:
        media_asset_id = await conn.fetchval(
            "SELECT id FROM media_assets WHERE storage_url=$1", media_asset_ref
        )
    playback_id = await conn.fetchval(
        """
        INSERT INTO playbacks (
            trigger_id, screen_id, ad_run_id, media_asset_id, organization_id, location_id, system_id,
            display_id, status, dispatched_at, started_at, ended_at,
            duration_ms, error_code, error_message
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::text::timestamptz,$11::text::timestamptz,$12::text::timestamptz,$13,$14,$15)
        ON CONFLICT (trigger_id, screen_id) DO UPDATE SET
            ad_run_id       = COALESCE(EXCLUDED.ad_run_id, playbacks.ad_run_id),
            media_asset_id  = COALESCE(EXCLUDED.media_asset_id, playbacks.media_asset_id),
            organization_id = COALESCE(EXCLUDED.organization_id, playbacks.organization_id),
            location_id     = COALESCE(EXCLUDED.location_id, playbacks.location_id),
            system_id       = COALESCE(EXCLUDED.system_id, playbacks.system_id),
            display_id      = COALESCE(EXCLUDED.display_id, playbacks.display_id),
            status          = EXCLUDED.status,
            dispatched_at   = COALESCE(EXCLUDED.dispatched_at, playbacks.dispatched_at),
            started_at      = COALESCE(EXCLUDED.started_at, playbacks.started_at),
            ended_at        = COALESCE(EXCLUDED.ended_at, playbacks.ended_at),
            duration_ms     = COALESCE(EXCLUDED.duration_ms, playbacks.duration_ms),
            error_code      = COALESCE(EXCLUDED.error_code, playbacks.error_code),
            error_message   = COALESCE(EXCLUDED.error_message, playbacks.error_message)
        RETURNING id
        """,
        env.trigger_id,
        env.screen_id,
        ad_run_id,
        media_asset_id,
        scope.organization_id,
        scope.location_id,
        scope.system_id,
        scope.display_id,
        env.status,
        env.payload_get("dispatched_at"),
        env.payload_get("started_at"),
        env.payload_get("ended_at"),
        env.payload_get("duration_ms"),
        env.payload_get("error_code"),
        env.payload_get("error_message"),
    )
    # FIX 2: hand the resolved ad_run id back so the fold can back-stamp events.ad_run_id.
    # Part B: also hand the playback id back so the fold can run the viewer_exposures
    # derivation as a post-projection step once the window closes.
    return {"ad_run_id": ad_run_id, "playback_id": playback_id}
