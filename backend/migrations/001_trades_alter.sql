-- Phase 0: scanner_trades schema alter
-- Adds the columns the collector was discarding from DegenClaw API responses:
-- openedAt, entryPrice, exitPrice, positionSize (as float), leverage, and a
-- natural dedup key via DegenClaw's own trade id. hold_time_s is a stored
-- generated column so it is always in sync with open/close timestamps.

ALTER TABLE scanner_trades
  ADD COLUMN IF NOT EXISTS opened_at_ms  BIGINT,
  ADD COLUMN IF NOT EXISTS closed_at_ms  BIGINT,
  ADD COLUMN IF NOT EXISTS entry_price   DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS exit_price    DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS position_size DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS leverage      SMALLINT,
  ADD COLUMN IF NOT EXISTS dgc_trade_id  TEXT;

ALTER TABLE scanner_trades
  ADD COLUMN IF NOT EXISTS hold_time_s INTEGER
    GENERATED ALWAYS AS ((closed_at_ms - opened_at_ms) / 1000) STORED;

CREATE UNIQUE INDEX IF NOT EXISTS scanner_trades_dgc_id_uk
  ON scanner_trades(agent_id, dgc_trade_id)
  WHERE dgc_trade_id IS NOT NULL;
