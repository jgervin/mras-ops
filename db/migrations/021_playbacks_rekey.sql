-- 021_playbacks_rekey.sql: decouple playbacks idempotency from device registration
--
-- Problem: the existing key UNIQUE(trigger_id, display_id) with display_id NOT NULL means
-- a playback INSERT fails for any unregistered display — the projector is wedged.
-- Fix: re-key to the raw screen_id string, mirroring the observation_tracks pattern.
-- display_id becomes nullable so it can be back-filled after resolution.

ALTER TABLE playbacks DROP CONSTRAINT playbacks_trigger_id_display_id_key;
ALTER TABLE playbacks ALTER COLUMN display_id DROP NOT NULL;
ALTER TABLE playbacks ALTER COLUMN screen_id  SET NOT NULL;
ALTER TABLE playbacks ADD CONSTRAINT playbacks_trigger_screen_key UNIQUE (trigger_id, screen_id);
