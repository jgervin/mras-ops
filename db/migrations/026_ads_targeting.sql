-- TODO-7: optional perception-targeting tags for ad selection.
-- Nullable, no default: the composer scores NULL as 0 (no re-rank), so every
-- existing ad and install behaves exactly as before.
-- Shape: {"moods": ["happy","sad",...], "objects": ["backpack",...]}
ALTER TABLE ads ADD COLUMN targeting jsonb;
COMMENT ON COLUMN ads.targeting IS
    'Perception targeting: {"moods":[],"objects":[]}; NULL = untargeted. '
    'Valid mood tokens (DeepFace 7 classes, as sent by mras-vision '
    'scene_context.viewer.mood): angry, disgust, fear, happy, sad, surprise, '
    'neutral. objects = lowercase YOLO labels ("person" is ignored by the '
    'selector).';
