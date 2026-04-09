-- Phase 0 cleanup: drop the legacy scanner_trades columns the new
-- collector never writes.
--
-- Post-deploy verification before applying:
--   SELECT COUNT(*) FROM scanner_trades WHERE opened_at_ms IS NULL
--     OR dgc_trade_id IS NULL;    -- must be 0
--   SELECT COUNT(*) FROM scanner_trades;    -- must match refetch total
--
-- Legacy 'size' column was TEXT (kept for full float precision in the
-- old composite dedup key); the new collector uses position_size
-- DOUBLE PRECISION. Legacy 'side' was derived from tradeType and always
-- "S" in practice. Legacy 'timestamp_ms' is now closed_at_ms.
-- Legacy 'idx_scanner_trades_agent_time' indexed (agent_id, timestamp_ms)
-- and is dropped together with the column.

DROP INDEX IF EXISTS idx_scanner_trades_agent_time;

ALTER TABLE scanner_trades DROP COLUMN IF EXISTS side;
ALTER TABLE scanner_trades DROP COLUMN IF EXISTS size;
ALTER TABLE scanner_trades DROP COLUMN IF EXISTS timestamp_ms;

-- Replace the legacy index with the new one covering closed_at_ms
-- (used heavily by the frontend trade table and analyzer's ORDER BY).
CREATE INDEX IF NOT EXISTS idx_scanner_trades_agent_closed
  ON scanner_trades(agent_id, closed_at_ms DESC);
