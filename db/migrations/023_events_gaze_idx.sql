-- Serves the gaze-join SELECT in derivations._gaze_attention
CREATE INDEX IF NOT EXISTS events_gaze_ts_idx
    ON events (ts)
    WHERE service = 'mras-vision' AND event_type = 'gaze' AND status = 'success';
