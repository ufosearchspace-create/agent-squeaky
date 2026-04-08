-- Phase 0: scanner_scores rewrite for Bayesian model
-- Drops the old D1..D6 + composite columns and adds the log-odds + evidence
-- log that the new scoring engine produces. Defaults are temporary and will
-- be dropped after the first successful analyzer run (see 007_*.sql).
--
-- The view scanner_agent_latest_scores is dropped first because it references
-- the D-columns. Migration 004 recreates it with the new schema.

DROP VIEW IF EXISTS scanner_agent_latest_scores;

ALTER TABLE scanner_scores DROP COLUMN IF EXISTS d1_timing;
ALTER TABLE scanner_scores DROP COLUMN IF EXISTS d2_sleep;
ALTER TABLE scanner_scores DROP COLUMN IF EXISTS d3_sizing;
ALTER TABLE scanner_scores DROP COLUMN IF EXISTS d4_reaction;
ALTER TABLE scanner_scores DROP COLUMN IF EXISTS d5_forum;
ALTER TABLE scanner_scores DROP COLUMN IF EXISTS d6_wallet;
ALTER TABLE scanner_scores DROP COLUMN IF EXISTS composite;

ALTER TABLE scanner_scores
  ADD COLUMN IF NOT EXISTS prior_log_odds     DOUBLE PRECISION NOT NULL DEFAULT 4.247927513443585,
  ADD COLUMN IF NOT EXISTS posterior_log_odds DOUBLE PRECISION NOT NULL DEFAULT 4.247927513443585,
  ADD COLUMN IF NOT EXISTS p_bot               DOUBLE PRECISION NOT NULL DEFAULT 0.95,
  ADD COLUMN IF NOT EXISTS evidence_log        JSONB NOT NULL DEFAULT '[]'::jsonb,
  ADD COLUMN IF NOT EXISTS hard_gates_hit      TEXT[] DEFAULT '{}',
  ADD COLUMN IF NOT EXISTS lr_version          INTEGER NOT NULL DEFAULT 1;
