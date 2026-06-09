CREATE TABLE IF NOT EXISTS components (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL, slug text NOT NULL UNIQUE,
    status text NOT NULL DEFAULT 'bundling' CHECK (status IN ('bundling','ready','failed')),
    error text, props_schema jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ads (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL, base_video text NOT NULL,
    component_id uuid NOT NULL REFERENCES components(id),
    default_props jsonb NOT NULL DEFAULT '{}'::jsonb,
    personalized_field text NOT NULL DEFAULT 'text',
    is_active boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ads_is_active_idx ON ads (is_active);
