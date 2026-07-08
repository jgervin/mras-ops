"""Per-camera live detection readings, derived from subject_observations.

face_count = number of observations for the camera in the last WINDOW_SECONDS;
confidence = average face_quality_score over that window (NULL -> 0.0).
subject_observations links to a camera via camera_id (there is no screen_id).
"""

WINDOW_SECONDS = 60


async def readings_for_system(conn, system_id) -> dict:
    rows = await conn.fetch(
        """
        SELECT c.id AS camera_id,
               COALESCE(o.face_count, 0)          AS face_count,
               COALESCE(o.confidence, 0)::float8  AS confidence
        FROM cameras c
        LEFT JOIN LATERAL (
            SELECT count(*) AS face_count, avg(so.face_quality_score) AS confidence
            FROM subject_observations so
            WHERE so.camera_id = c.id
              AND so.observed_at >= now() - make_interval(secs => $2)
        ) o ON true
        WHERE c.system_id = $1
        """,
        system_id, WINDOW_SECONDS,
    )
    return {
        str(r["camera_id"]): {"face_count": r["face_count"], "confidence": r["confidence"]}
        for r in rows
    }
