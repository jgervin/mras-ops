-- Demo seed: two active personalized ads so round-2 peel-back shows a genuine A/B
-- on the two peel screens (instead of repeating one base ad).
--
-- Both ads render through the real registered Remotion composition `comp-helloname`
-- (mras-overlays src/custom/helloname.tsx — schema: { text, color }). The selector
-- overwrites `text` with the subject's display_name; `color` + `base_video` differ
-- per ad so the two peel screens are visually distinct.
--
-- Apply against the dev DB:
--   docker compose exec -T postgres psql -U mras -d mras -f - < db/seeds/001_demo_ab_ads.sql
-- Idempotent: safe to re-run (component upserts on slug; ads skipped if the named
-- rows already exist — no FK-breaking deletes).
--
-- NOTE (by design): once active custom ads exist, the OPENER also draws from this
-- table — select() picks the newest (created_at DESC LIMIT 1), so the 4-screen
-- opener will render that ad's base_video too. Only round 2 does the A/B split.

WITH comp AS (
  INSERT INTO components (name, slug, status)
  VALUES ('Hello Name', 'helloname', 'ready')
  ON CONFLICT (slug) DO UPDATE SET status = 'ready'
  RETURNING id
)
INSERT INTO ads (name, base_video, component_id, default_props, personalized_field, is_active)
SELECT v.name, v.base_video, comp.id, v.default_props, v.personalized_field, v.is_active
FROM comp,
(VALUES
  ('Demo Promo A (cyan / standard2)',  '/assets/standard2.mp4', '{"color":"#00e5ff"}'::jsonb, 'text', true),
  ('Demo Promo B (amber / standard3)', '/assets/standard3.mp4', '{"color":"#ffb300"}'::jsonb, 'text', true)
) AS v(name, base_video, default_props, personalized_field, is_active)
WHERE NOT EXISTS (SELECT 1 FROM ads a2 WHERE a2.name = v.name);
