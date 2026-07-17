-- 029: persist the raw Remotion .tsx uploaded via POST /components so god view
-- can display it inside the Composition node. Nullable: components ingested
-- before this migration have no stored source (re-ingest to populate).
ALTER TABLE components ADD COLUMN source text;
