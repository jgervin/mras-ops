CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TYPE device_status      AS ENUM ('active','degraded','offline','retired');
CREATE TYPE lifecycle_status   AS ENUM ('planned','active','inactive','degraded','offline','retired');
CREATE TYPE embedding_status   AS ENUM ('pending','active','rejected','expired','deleted');
CREATE TYPE embedding_type     AS ENUM ('face');
CREATE TYPE profile_status     AS ENUM ('anonymous','known','merged','blocklisted','deleted');
CREATE TYPE role_label         AS ENUM (
  'Customer.OptInIdentified','Customer.Anonymous','Customer.Blocklisted',
  'Host.IT','Advertiser.ReadOnly','AgencyOfRecord.Standard',
  'Operator.SystemAdmin','Operator.SeniorSystemAdmin');
CREATE TYPE organization_type  AS ENUM ('platform_operator','host','advertiser','agency_of_record','partner','vendor');
CREATE TYPE decision_type      AS ENUM ('identity','demographic','contextual','scheduled','manual','fallback','blocked_suppressed','error_recovery');
CREATE TYPE ad_run_status      AS ENUM ('planned','composing','ready','dispatched','playing','completed','failed','canceled');
CREATE TYPE playback_status    AS ENUM ('dispatched','started','ended','failed','interrupted','unknown');
CREATE TYPE composition_status AS ENUM ('queued','selected','rendering','rendered','failed','canceled');
CREATE TYPE exposure_role      AS ENUM ('target','viewer','bystander','possible_viewer');
CREATE TYPE identity_status    AS ENUM ('known','anonymous','unmatched','suppressed');
CREATE TYPE match_status       AS ENUM ('matched','no_match','below_threshold','suppressed','blocked');
CREATE TYPE blocklist_type     AS ENUM ('biometric_opt_out','personalization_opt_out','legal_hold','manual_suppression','safety');
CREATE TYPE scope_level        AS ENUM ('global','organization','location','system');
CREATE TYPE asset_type         AS ENUM ('image','frame','clip','video','audio','thumbnail','composite','model_artifact');
CREATE TYPE device_type        AS ENUM ('camera','display','edge_node','player','sensor');
CREATE TYPE camera_role        AS ENUM ('detection','enrollment','audience_measurement','security_context');
CREATE TYPE display_role       AS ENUM ('primary_ad','secondary_ad','ambient','status');
CREATE TYPE location_type      AS ENUM ('country','region','city','district','campus','building','mall','airport','venue','store','floor','zone','area');
CREATE TYPE system_type        AS ENUM ('onsite_mras','demo','lab','kiosk_cluster','edge_node');
CREATE TYPE participant_role   AS ENUM ('host','tenant','operator','advertiser','agency','maintenance_partner','reporting_viewer');
CREATE TYPE enrollment_scope   AS ENUM ('global','organization','location','system');
CREATE TYPE enrollment_source  AS ENUM ('manual_upload','crm_import','loyalty_import','admin_created','camera_enroll','camera_match');
CREATE TYPE render_mode        AS ENUM ('prebuilt','template_overlay','remotion','ffmpeg','genai_video','fallback');
CREATE TYPE personalization_type AS ENUM ('none','demographic','contextual','identity','name','likeness','hybrid','fallback','suppressed');
CREATE TYPE model_run_type     AS ENUM ('recognition','demographic_estimation','mood_detection','gaze_detection','ad_decision','video_generation','voice_generation','object_detection','composition');
CREATE TYPE actor_type         AS ENUM ('user','system','model','api');
CREATE TYPE detection_type     AS ENUM ('face','body','eyes','group','demographic','object');
CREATE TYPE observation_match  AS ENUM ('no_match','matched_known','matched_anonymous','new_anonymous','suppressed','ignored');
