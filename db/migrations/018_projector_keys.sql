-- 018_projector_keys.sql: idempotency unique keys for the 5 projector-written
-- summary tables that shipped with only a uuid PK. Without these a projector
-- replay/rebuild double-counts (breaks Decision 3). Each key's discriminator
-- columns are NOT NULL — a UNIQUE over a nullable column does not enforce
-- uniqueness (NULLs are distinct); same lesson as ad_runs.trigger_id.

-- observation_tracks: a track = its camera + the tracker's track id. Key on the
-- RAW camera screen_id string (always present in the event) rather than the
-- resolved camera_id uuid, so idempotency does not depend on device-registry
-- resolution (an unregistered screen_id resolves to a null uuid).
ALTER TABLE observation_tracks ADD COLUMN camera_screen_id text NOT NULL;
ALTER TABLE observation_tracks ALTER COLUMN camera_track_id SET NOT NULL;
ALTER TABLE observation_tracks ADD CONSTRAINT observation_tracks_track_key
    UNIQUE (camera_screen_id, camera_track_id);

-- identity_matches: N ranked candidates per observation.
ALTER TABLE identity_matches ALTER COLUMN rank SET NOT NULL;
ALTER TABLE identity_matches ADD CONSTRAINT identity_matches_obs_rank_key
    UNIQUE (subject_observation_id, rank);

-- personalization_decisions: one decision per source event.
ALTER TABLE personalization_decisions ALTER COLUMN event_id SET NOT NULL;
ALTER TABLE personalization_decisions ADD CONSTRAINT personalization_decisions_event_key
    UNIQUE (event_id);

-- composition_runs: one composition per trigger.
ALTER TABLE composition_runs ALTER COLUMN trigger_id SET NOT NULL;
ALTER TABLE composition_runs ADD CONSTRAINT composition_runs_trigger_key
    UNIQUE (trigger_id);

-- viewer_exposures: one exposure row per (ad_run, observed viewer).
ALTER TABLE viewer_exposures ALTER COLUMN subject_observation_id SET NOT NULL;
ALTER TABLE viewer_exposures ADD CONSTRAINT viewer_exposures_adrun_obs_key
    UNIQUE (ad_run_id, subject_observation_id);
