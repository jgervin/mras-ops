-- Append-only journal (D19). Keeps bigserial id as the projector cursor (Decision 8).
CREATE TABLE events (
    id          bigserial PRIMARY KEY,
    trigger_id  uuid NOT NULL,
    ts          timestamptz NOT NULL DEFAULT now(),
    service     text NOT NULL,
    event_type  text NOT NULL,
    status      text NOT NULL,
    payload     jsonb NOT NULL DEFAULT '{}',
    asset_ref   text,
    -- Decision 2: first-class scope columns (nullable; stamped by writers going forward)
    organization_id uuid REFERENCES organizations(id),
    location_id uuid REFERENCES locations(id),
    system_id   uuid REFERENCES systems(id),
    display_id  uuid REFERENCES displays(id),
    camera_id   uuid REFERENCES cameras(id),
    subject_profile_id uuid REFERENCES subject_profiles(id),
    ad_run_id   uuid REFERENCES ad_runs(id)
);

CREATE TABLE audit_logs (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_user_id uuid,
    actor_type  actor_type NOT NULL,
    action      text NOT NULL,
    entity_type text NOT NULL,
    entity_id   text NOT NULL,
    before jsonb, after jsonb, ip_address text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Resolve deferred event_id FKs from 013/015 (events did not exist yet).
ALTER TABLE subject_observations
    ADD CONSTRAINT subject_observations_event_fk
    FOREIGN KEY (event_id) REFERENCES events(id);

ALTER TABLE personalization_decisions
    ADD CONSTRAINT personalization_decisions_event_fk
    FOREIGN KEY (event_id) REFERENCES events(id);
