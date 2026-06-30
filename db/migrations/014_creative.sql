CREATE TABLE media_assets (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid REFERENCES organizations(id),
    asset_type  asset_type NOT NULL,
    storage_url text NOT NULL,
    mime_type text, duration_ms int, width int, height int,
    source      text NOT NULL,
    sha256_hash text,
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE campaigns (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid REFERENCES organizations(id),
    name        text NOT NULL,
    status      lifecycle_status NOT NULL DEFAULT 'active',
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE campaign_rules (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    campaign_id uuid NOT NULL REFERENCES campaigns(id),
    rule        jsonb NOT NULL DEFAULT '{}',
    priority    int NOT NULL DEFAULT 0,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- components/ads keep their shipped shape (002_custom_components.sql), now uuid-consistent + FKs into the creative model.
CREATE TABLE components (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL, slug text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'bundling' CHECK (status IN ('bundling','ready','failed')),
    error text, props_schema jsonb, created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE ads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL, base_video text NOT NULL,
    component_id uuid NOT NULL REFERENCES components(id),
    campaign_id uuid REFERENCES campaigns(id),
    default_props jsonb NOT NULL DEFAULT '{}'::jsonb,
    personalized_field text NOT NULL DEFAULT 'text',
    is_active boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ads_is_active_idx ON ads (is_active);

CREATE TABLE ad_creatives (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ad_id       uuid NOT NULL REFERENCES ads(id),
    media_asset_id uuid REFERENCES media_assets(id),
    variant     text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE creative_approvals (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    ad_id       uuid NOT NULL REFERENCES ads(id),
    status      text NOT NULL DEFAULT 'pending',
    reviewed_by_user_id uuid,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Resolve the deferred asset FKs from 013 (media_assets did not exist yet).
ALTER TABLE subject_profiles
    ADD CONSTRAINT subject_profiles_photo_fk
    FOREIGN KEY (primary_photo_asset_id) REFERENCES media_assets(id);
ALTER TABLE subject_observations
    ADD CONSTRAINT subject_observations_frame_fk
    FOREIGN KEY (frame_asset_id) REFERENCES media_assets(id);
ALTER TABLE subject_observations
    ADD CONSTRAINT subject_observations_clip_fk
    FOREIGN KEY (clip_asset_id) REFERENCES media_assets(id);
ALTER TABLE subject_embeddings
    ADD CONSTRAINT subject_embeddings_asset_fk
    FOREIGN KEY (source_asset_id) REFERENCES media_assets(id);
