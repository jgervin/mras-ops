-- Phase 0 initial schema: identities, events, campaigns
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- identities: enrolled people, shared by mras-vision and mras-composer
CREATE TABLE IF NOT EXISTS identities (
    uuid             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    name             text        NOT NULL,
    embedding        float4[],
    embedding_status text        NOT NULL DEFAULT 'pending',
    is_blocked       boolean     NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS identities_name_idx             ON identities (name);
CREATE INDEX IF NOT EXISTS identities_embedding_status_idx ON identities (embedding_status);

-- events: append-only event log (architecture decision D19)
CREATE TABLE IF NOT EXISTS events (
    id          bigserial   PRIMARY KEY,
    trigger_id  uuid        NOT NULL,
    ts          timestamptz NOT NULL DEFAULT now(),
    service     text        NOT NULL,
    event_type  text        NOT NULL,
    status      text        NOT NULL,
    payload     jsonb       NOT NULL DEFAULT '{}',
    asset_ref   text
);

CREATE INDEX IF NOT EXISTS events_ts_desc_idx    ON events (ts DESC);
CREATE INDEX IF NOT EXISTS events_trigger_id_idx ON events (trigger_id);

-- campaigns: minimal Phase 0 campaign table
CREATE TABLE IF NOT EXISTS campaigns (
    id               serial  PRIMARY KEY,
    name             text    NOT NULL,
    base_video_path  text    NOT NULL,
    tts_template     text    NOT NULL DEFAULT 'Welcome, {name}!',
    is_active        boolean NOT NULL DEFAULT true
);
