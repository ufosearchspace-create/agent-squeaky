-- Phase 0 follow-up: PostgREST ON CONFLICT requires a true UNIQUE
-- CONSTRAINT, not a partial unique INDEX. Migration 001 added
-- `scanner_trades_dgc_id_uk` as a partial unique index with the
-- predicate `WHERE dgc_trade_id IS NOT NULL`, but Supabase-py upserts
-- against `on_conflict=agent_id,dgc_trade_id` returned 400 at runtime
-- because PostgREST cannot name a partial index as the conflict arbiter.
--
-- Fix: drop the partial index and add a real table-level UNIQUE
-- constraint on (agent_id, dgc_trade_id). This is only safe AFTER
-- TRUNCATE scanner_trades (legacy rows have NULL dgc_trade_id which
-- would violate a plain UNIQUE, and we were refetching those rows from
-- the DegenClaw API anyway in TG4).
--
-- IMPORTANT: Run `TRUNCATE scanner_trades` BEFORE applying this
-- migration.

DROP INDEX IF EXISTS scanner_trades_dgc_id_uk;

ALTER TABLE scanner_trades
  ADD CONSTRAINT scanner_trades_agent_dgc_id_uk
  UNIQUE (agent_id, dgc_trade_id);
