-- 022_viewer_exposures_perf.sql
-- FIX 5 (DBA): the viewer_exposures derivation's bystander half joins
-- subject_observations by (system_id, observed_at BETWEEN started_at AND ended_at)
-- for every closed playback window. No index covered that predicate (013 indexes
-- only observation_track_id; 017 covers events, not subject_observations), so the
-- window join fell back to a scan. Add the composite index so the co-scope +
-- time-window lookup is index-served.
CREATE INDEX IF NOT EXISTS subject_observations_system_observed_idx
    ON subject_observations (system_id, observed_at);
