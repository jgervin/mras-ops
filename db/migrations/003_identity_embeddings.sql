-- Adaptive enrollment: multi-embedding gallery per identity.
CREATE TABLE IF NOT EXISTS identity_embeddings (
    id            uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    identity_uuid uuid        NOT NULL REFERENCES identities(uuid) ON DELETE CASCADE,
    embedding     float4[]    NOT NULL,
    source        text        NOT NULL CHECK (source IN ('enroll', 'auto')),
    quality       real,
    provenance    jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS identity_embeddings_uuid_idx   ON identity_embeddings (identity_uuid);
CREATE INDEX IF NOT EXISTS identity_embeddings_source_idx ON identity_embeddings (source);

-- Backfill: each existing identity's single embedding becomes its first enroll anchor.
INSERT INTO identity_embeddings (identity_uuid, embedding, source, provenance)
SELECT uuid, embedding, 'enroll', jsonb_build_object('backfill', true)
FROM identities
WHERE embedding IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM identity_embeddings e WHERE e.identity_uuid = identities.uuid
  );
