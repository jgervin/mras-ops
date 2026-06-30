-- Preserve the Phase-0 access paths.
CREATE INDEX events_ts_desc_idx    ON events (ts DESC);
CREATE INDEX events_trigger_id_idx ON events (trigger_id);
-- Decision 2: scoped live-feed + drilldown shapes.
CREATE INDEX events_system_ts_idx   ON events (system_id, ts DESC);
CREATE INDEX events_location_ts_idx ON events (location_id, ts DESC);
CREATE INDEX events_ad_run_idx      ON events (ad_run_id) WHERE ad_run_id IS NOT NULL;
