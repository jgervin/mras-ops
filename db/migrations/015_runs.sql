-- 015_runs.sql: decision / run / playback / exposure tables
-- Decision 3 idempotency keys: UNIQUE(trigger_id) on ad_runs, UNIQUE(trigger_id, display_id) on playbacks
-- Decision 12: target_watched boolean on ad_runs; watch_probability numeric on viewer_exposures
-- Deferred FK order: personalization_decisions created first (model_run_id plain uuid),
--   then model_runs created, then ALTER TABLE adds the FK back to model_runs.
--   event_id is plain bigint with no FK (events table added in 016).

CREATE TABLE personalization_decisions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_id  uuid,
    event_id    bigint,                     -- FK added in 016
    organization_id uuid REFERENCES organizations(id),
    location_id uuid REFERENCES locations(id),
    system_id   uuid REFERENCES systems(id),
    campaign_id uuid REFERENCES campaigns(id),
    selected_ad_id uuid REFERENCES ads(id),
    selected_creative_id uuid REFERENCES ad_creatives(id),
    decision_type decision_type NOT NULL,
    target_subject_profile_id uuid REFERENCES subject_profiles(id),
    target_observation_id uuid REFERENCES subject_observations(id),
    identity_confidence numeric, demographic_confidence numeric, decision_confidence numeric,
    decision_factors jsonb NOT NULL DEFAULT '{}',
    prompt_used text,
    model_run_id uuid,                      -- FK added later in this file
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE model_runs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type    model_run_type NOT NULL,
    provider text, model_name text, model_version text,
    input_asset_id uuid REFERENCES media_assets(id),
    output_asset_id uuid REFERENCES media_assets(id),
    prompt_template_id uuid, prompt_text text, parameters jsonb,
    latency_ms int, cost_estimate numeric,
    status      text NOT NULL DEFAULT 'success',
    error_code text, error_message text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE personalization_decisions
    ADD CONSTRAINT personalization_decisions_model_fk
    FOREIGN KEY (model_run_id) REFERENCES model_runs(id);

CREATE TABLE composition_runs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_id  uuid,
    personalization_decision_id uuid REFERENCES personalization_decisions(id),
    organization_id uuid REFERENCES organizations(id),
    location_id uuid REFERENCES locations(id),
    system_id   uuid REFERENCES systems(id),
    ad_id       uuid REFERENCES ads(id),
    component_id uuid REFERENCES components(id),
    render_mode render_mode NOT NULL DEFAULT 'prebuilt',
    status      composition_status NOT NULL DEFAULT 'queued',
    input_asset_id  uuid REFERENCES media_assets(id),
    output_asset_id uuid REFERENCES media_assets(id),
    used_spoken_name boolean NOT NULL DEFAULT false,
    used_visible_name boolean NOT NULL DEFAULT false,
    used_likeness boolean NOT NULL DEFAULT false,
    used_voice_clone boolean NOT NULL DEFAULT false,
    render_progress numeric, error_code text, error_message text,
    started_at timestamptz, ended_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ad_runs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    trigger_id  uuid,
    organization_id uuid REFERENCES organizations(id),
    location_id uuid REFERENCES locations(id),
    system_id   uuid REFERENCES systems(id),
    display_id  uuid REFERENCES displays(id),
    campaign_id uuid REFERENCES campaigns(id),
    ad_id       uuid REFERENCES ads(id),
    personalization_decision_id uuid REFERENCES personalization_decisions(id),
    composition_run_id uuid REFERENCES composition_runs(id),
    target_subject_profile_id uuid REFERENCES subject_profiles(id),
    personalization_type personalization_type NOT NULL DEFAULT 'none',
    used_spoken_name boolean NOT NULL DEFAULT false,
    used_visible_name boolean NOT NULL DEFAULT false,
    used_likeness boolean NOT NULL DEFAULT false,
    used_voice_clone boolean NOT NULL DEFAULT false,
    target_watched boolean,                 -- exact via trigger_id (Decision 12)
    target_watch_probability numeric,
    estimated_total_viewers int, estimated_identified_viewers int, estimated_anonymous_viewers int,
    status      ad_run_status NOT NULL DEFAULT 'planned',
    started_at timestamptz, ended_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trigger_id)                      -- Decision 3: one ad_run per trigger
);

CREATE TABLE playbacks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ad_run_id   uuid REFERENCES ad_runs(id),
    composition_run_id uuid REFERENCES composition_runs(id),
    trigger_id  uuid,
    organization_id uuid REFERENCES organizations(id),
    location_id uuid REFERENCES locations(id),
    system_id   uuid REFERENCES systems(id),
    display_id  uuid NOT NULL REFERENCES displays(id),
    media_asset_id uuid REFERENCES media_assets(id),
    screen_id   text,
    status      playback_status NOT NULL DEFAULT 'dispatched',
    dispatched_at timestamptz, started_at timestamptz, ended_at timestamptz,
    duration_ms int, error_code text, error_message text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (trigger_id, display_id)          -- Decision 3: one playback per trigger+display
);

CREATE TABLE viewer_exposures (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ad_run_id   uuid NOT NULL REFERENCES ad_runs(id),
    playback_id uuid REFERENCES playbacks(id),
    subject_profile_id uuid REFERENCES subject_profiles(id),
    subject_observation_id uuid REFERENCES subject_observations(id),
    observation_track_id uuid REFERENCES observation_tracks(id),
    role        exposure_role NOT NULL,
    identity_status identity_status NOT NULL DEFAULT 'unmatched',
    identity_confidence numeric,
    watch_probability numeric,              -- bystanders (Decision 12)
    watched     boolean,                    -- targets only, exact via trigger_id
    gaze_duration_ms int, visible_duration_ms int, attending_fraction numeric,
    distance_estimate_m numeric,
    mood_label text, mood_confidence numeric, expression_label text, expression_confidence numeric,
    demographic_snapshot jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX viewer_exposures_ad_run_idx ON viewer_exposures (ad_run_id);
