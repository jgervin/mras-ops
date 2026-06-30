CREATE TABLE subject_profiles (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid REFERENCES organizations(id),
    global_subject_key text,
    status      profile_status NOT NULL DEFAULT 'anonymous',
    display_name text, first_name text, last_name text,
    external_customer_id text,
    primary_photo_asset_id uuid,            -- FK added in 014 (media_assets)
    first_seen_at timestamptz, last_seen_at timestamptz,
    created_from_observation_id uuid,
    confidence_level text,
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE identity_enrollments (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_profile_id uuid NOT NULL REFERENCES subject_profiles(id),
    organization_id uuid REFERENCES organizations(id),
    location_id uuid REFERENCES locations(id),
    system_id   uuid REFERENCES systems(id),
    enrollment_scope enrollment_scope NOT NULL DEFAULT 'system',
    source      enrollment_source NOT NULL,
    external_person_id text,
    consent_status text,
    status      lifecycle_status NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE observation_tracks (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    system_id   uuid REFERENCES systems(id),
    camera_id   uuid REFERENCES cameras(id),
    subject_profile_id uuid REFERENCES subject_profiles(id),
    camera_track_id text,
    started_at timestamptz, ended_at timestamptz,
    max_identity_confidence numeric, track_confidence numeric,
    observation_count int NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE subject_observations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    event_id    bigint,                     -- FK added in 016 (events)
    trigger_id  uuid,
    organization_id uuid REFERENCES organizations(id),
    location_id uuid REFERENCES locations(id),
    system_id   uuid REFERENCES systems(id),
    camera_id   uuid REFERENCES cameras(id),
    observation_track_id uuid REFERENCES observation_tracks(id),
    frame_asset_id uuid,                    -- FK added in 014
    clip_asset_id  uuid,                    -- FK added in 014
    observed_at timestamptz NOT NULL,
    detection_type detection_type NOT NULL,
    subject_profile_id uuid REFERENCES subject_profiles(id),
    camera_track_id text,
    bounding_box jsonb, face_quality_score numeric, identity_confidence numeric,
    demographic_snapshot jsonb, mood_snapshot jsonb, attention_snapshot jsonb,
    match_status observation_match NOT NULL DEFAULT 'no_match',
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (event_id)
);
CREATE INDEX subject_observations_track_idx ON subject_observations (observation_track_id);

CREATE TABLE subject_embeddings (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_profile_id uuid NOT NULL REFERENCES subject_profiles(id),
    identity_enrollment_id uuid REFERENCES identity_enrollments(id),
    qdrant_collection text NOT NULL,
    qdrant_point_id   text NOT NULL,
    embedding_type embedding_type NOT NULL DEFAULT 'face',
    source      text NOT NULL,
    source_asset_id uuid,                   -- FK added in 014
    source_observation_id uuid REFERENCES subject_observations(id),
    quality_score numeric, model_name text, model_version text,
    status      embedding_status NOT NULL DEFAULT 'pending',
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz
);
CREATE INDEX subject_embeddings_active_idx ON subject_embeddings (subject_profile_id) WHERE status = 'active';

CREATE TABLE identity_matches (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_observation_id uuid NOT NULL REFERENCES subject_observations(id),
    candidate_subject_profile_id uuid REFERENCES subject_profiles(id),
    candidate_embedding_id uuid REFERENCES subject_embeddings(id),
    match_status match_status NOT NULL,
    confidence numeric, threshold numeric,
    model_name text, model_version text, qdrant_score numeric, rank int,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE subject_profile_merges (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_subject_profile_id uuid NOT NULL REFERENCES subject_profiles(id),
    to_subject_profile_id   uuid NOT NULL REFERENCES subject_profiles(id),
    merge_reason text NOT NULL,
    confidence numeric,
    merged_by_user_id uuid,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE blocklist_entries (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_profile_id uuid REFERENCES subject_profiles(id),
    organization_id uuid REFERENCES organizations(id),
    location_id uuid REFERENCES locations(id),
    system_id   uuid REFERENCES systems(id),
    scope       scope_level NOT NULL DEFAULT 'global',
    blocklist_type blocklist_type NOT NULL,
    status      lifecycle_status NOT NULL DEFAULT 'active',
    reason      text,
    created_by_user_id uuid,
    starts_at timestamptz NOT NULL DEFAULT now(), ends_at timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
