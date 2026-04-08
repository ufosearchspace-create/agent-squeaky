-- Phase 0 transition: relax NOT NULL on legacy columns (side, size, timestamp_ms)
-- and drop the old composite unique constraint so that the new collector can
-- insert rows that only populate the new schema fields (opened_at_ms,
-- closed_at_ms, dgc_trade_id, ...). The legacy columns themselves are kept
-- until TG4's TRUNCATE + refetch completes, then fully dropped by migration
-- 005_trades_drop_legacy.sql.

ALTER TABLE scanner_trades ALTER COLUMN side         DROP NOT NULL;
ALTER TABLE scanner_trades ALTER COLUMN size         DROP NOT NULL;
ALTER TABLE scanner_trades ALTER COLUMN timestamp_ms DROP NOT NULL;

-- The old composite unique constraint becomes redundant once dgc_trade_id
-- is the authoritative dedup key. Drop it so NULL inserts on timestamp_ms
-- do not conflict (NULL in a unique constraint is allowed, but the old
-- constraint's semantics no longer fit our transition state).
ALTER TABLE scanner_trades
  DROP CONSTRAINT IF EXISTS scanner_trades_agent_id_timestamp_ms_coin_side_size_key;

-- The non-unique legacy index on (agent_id, timestamp_ms) is still useful
-- for any historical queries and doesn't cost much — keep for now, drop
-- in 005_trades_drop_legacy.sql together with the column.
