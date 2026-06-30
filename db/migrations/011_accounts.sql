CREATE TABLE organizations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name        text NOT NULL,
    organization_type organization_type NOT NULL,
    status      lifecycle_status NOT NULL DEFAULT 'active',
    parent_organization_id uuid REFERENCES organizations(id),
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE organization_relationships (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    from_organization_id uuid NOT NULL REFERENCES organizations(id),
    to_organization_id   uuid NOT NULL REFERENCES organizations(id),
    relationship text NOT NULL,
    status      lifecycle_status NOT NULL DEFAULT 'active',
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Decision 1: Supabase Auth owns users + JWT role claims; Postgres holds only a
-- thin scope map for row-level filtering. user_id is the Supabase auth subject.
CREATE TABLE user_org_scopes (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         uuid NOT NULL,
    organization_id uuid NOT NULL REFERENCES organizations(id),
    location_id     uuid,
    role            role_label NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, organization_id, role)
);
CREATE INDEX user_org_scopes_user_idx ON user_org_scopes (user_id);
