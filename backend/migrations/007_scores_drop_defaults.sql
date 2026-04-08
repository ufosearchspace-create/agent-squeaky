-- Phase 0 cleanup: drop the temporary DEFAULTs on scanner_scores.
--
-- 002_scores_rewrite.sql added DEFAULT 4.247... / 0.95 / '[]' / 1 to the
-- new Bayesian columns so that any leftover insert from the old analyzer
-- image would not fail during the Railway deploy window. Once the new
-- analyzer has been running cleanly for at least 48h (typically 1-2 cron
-- cycles after the Vercel+Railway deploy), those defaults are obsolete —
-- every insert will come from the new code and always populate all five
-- columns explicitly.
--
-- IMPORTANT: apply this migration only AFTER:
--   1. The new collector + analyzer have been live on Railway for a full
--      30-minute cycle with no schema errors in the logs.
--   2. `SELECT COUNT(*) FROM scanner_scores WHERE scored_at > NOW() - INTERVAL '1 hour'`
--      shows >0 fresh rows that carry non-default values.
-- Until then, keeping the defaults lets the DB recover gracefully if
-- Railway rolls back to the old image.

ALTER TABLE scanner_scores
  ALTER COLUMN prior_log_odds DROP DEFAULT,
  ALTER COLUMN posterior_log_odds DROP DEFAULT,
  ALTER COLUMN p_bot DROP DEFAULT,
  ALTER COLUMN evidence_log DROP DEFAULT,
  ALTER COLUMN lr_version DROP DEFAULT;
