-- PR3: scanner_onchain schema tweak
-- Add the columns the Basescan enricher needs that are not already
-- present from migration 003. balance_usd and address_kind are new;
-- the rest already exist (owner_wallet, age_days, total_tx_count,
-- chains_active, last_refreshed_at, source).

ALTER TABLE scanner_onchain
  ADD COLUMN IF NOT EXISTS balance_usd DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS address_kind TEXT;
