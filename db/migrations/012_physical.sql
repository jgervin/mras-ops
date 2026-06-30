CREATE TABLE locations (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    parent_location_id uuid REFERENCES locations(id),
    name        text NOT NULL,
    location_type location_type NOT NULL,
    country text, region text, state text, city text, address text,
    lat numeric, lng numeric, timezone text,
    status      lifecycle_status NOT NULL DEFAULT 'active',
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX locations_parent_idx ON locations (parent_location_id);

CREATE TABLE location_participants (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    location_id uuid NOT NULL REFERENCES locations(id),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    role        participant_role NOT NULL,
    status      lifecycle_status NOT NULL DEFAULT 'active',
    starts_at timestamptz, ends_at timestamptz,
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE systems (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    location_id uuid NOT NULL REFERENCES locations(id),
    name        text NOT NULL,
    system_type system_type NOT NULL DEFAULT 'onsite_mras',
    zone text, floor text, lat numeric, lng numeric, timezone text,
    status      lifecycle_status NOT NULL DEFAULT 'active',
    config      jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX systems_location_idx ON systems (location_id);

CREATE TABLE devices (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    system_id   uuid NOT NULL REFERENCES systems(id),
    location_id uuid REFERENCES locations(id),
    device_type device_type NOT NULL,
    name        text NOT NULL,
    external_device_key text, serial_number text,
    status      device_status NOT NULL DEFAULT 'active',
    last_seen_at timestamptz, lat numeric, lng numeric, zone text, floor text,
    metadata    jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE cameras (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id   uuid REFERENCES devices(id),
    system_id   uuid NOT NULL REFERENCES systems(id),
    location_id uuid REFERENCES locations(id),
    name        text,
    camera_role camera_role NOT NULL DEFAULT 'detection',
    stream_url  text,
    screen_id   text,            -- runtime string the vision service emits (e.g. 'screen_0')
    status      device_status NOT NULL DEFAULT 'active',
    last_seen_at timestamptz, calibration jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX cameras_screen_id_idx ON cameras (screen_id);

CREATE TABLE displays (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id   uuid REFERENCES devices(id),
    system_id   uuid NOT NULL REFERENCES systems(id),
    location_id uuid REFERENCES locations(id),
    name        text,
    screen_id   text NOT NULL,   -- runtime string the kiosk emits (e.g. 'display-2')
    display_role display_role NOT NULL DEFAULT 'primary_ad',
    resolution_width int, resolution_height int,
    status      device_status NOT NULL DEFAULT 'active',
    last_seen_at timestamptz, calibration jsonb NOT NULL DEFAULT '{}',
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX displays_screen_id_idx ON displays (screen_id);

CREATE TABLE device_health_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    device_id   uuid NOT NULL REFERENCES devices(id),
    status      device_status NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}',
    observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX device_health_device_idx ON device_health_events (device_id, observed_at DESC);

CREATE TABLE system_health_events (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    system_id   uuid NOT NULL REFERENCES systems(id),
    status      lifecycle_status NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}',
    observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX system_health_system_idx ON system_health_events (system_id, observed_at DESC);
