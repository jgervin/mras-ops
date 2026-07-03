-- 024_target_attribution_idx.sql
-- DBA review: the viewer_exposures derivation's TARGET-attribution queries run on
-- every closed playback inside the single-writer fold, with no index support:
--   * PRIMARY  (derivations.py): WHERE trigger_id=$1 ORDER BY observed_at, id LIMIT 1
--   * FALLBACK (derivations.py): WHERE subject_profile_id=$1 AND system_id=$2
--                                AND observed_at<=$3 ORDER BY observed_at DESC, id DESC LIMIT 1
-- 013 indexes only observation_track_id; 022 covers (system_id, observed_at) for the
-- bystander half but does not lead with subject_profile_id or cover trigger_id. Add
-- partial composite indexes so both target lookups are index-served; the WHERE
-- clauses skip the (majority) rows that can never match.
CREATE INDEX IF NOT EXISTS subject_observations_profile_system_observed_idx
    ON subject_observations (subject_profile_id, system_id, observed_at DESC)
    WHERE subject_profile_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS subject_observations_trigger_idx
    ON subject_observations (trigger_id)
    WHERE trigger_id IS NOT NULL;
