-- Phase 0: new tables for candles (Phase 3), on-chain enrichment (Phase 4),
-- manual labels for ground truth, and versioned signal LRs. All four tables
-- have RLS enabled with a public-read policy to match the existing pattern.

CREATE TABLE IF NOT EXISTS scanner_candles (
  coin       TEXT NOT NULL,
  interval   TEXT NOT NULL,
  ts_ms      BIGINT NOT NULL,
  open       DOUBLE PRECISION,
  high       DOUBLE PRECISION,
  low        DOUBLE PRECISION,
  close      DOUBLE PRECISION,
  volume     DOUBLE PRECISION,
  fetched_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (coin, interval, ts_ms)
);
CREATE INDEX IF NOT EXISTS scanner_candles_coin_ts ON scanner_candles(coin, ts_ms);
ALTER TABLE scanner_candles ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Public read" ON scanner_candles;
CREATE POLICY "Public read" ON scanner_candles FOR SELECT USING (true);

CREATE TABLE IF NOT EXISTS scanner_onchain (
  owner_wallet            TEXT PRIMARY KEY,
  base_first_tx_date      DATE,
  age_days                INTEGER,
  total_tx_count          INTEGER,
  chains_active           SMALLINT,
  defi_interaction_count  INTEGER,
  nft_count               INTEGER,
  has_ens                 BOOLEAN DEFAULT FALSE,
  activity_score          REAL,
  last_refreshed_at       TIMESTAMPTZ DEFAULT NOW(),
  source                  TEXT
);
ALTER TABLE scanner_onchain ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Public read" ON scanner_onchain;
CREATE POLICY "Public read" ON scanner_onchain FOR SELECT USING (true);

CREATE TABLE IF NOT EXISTS scanner_labels (
  agent_id    TEXT PRIMARY KEY REFERENCES scanner_agents(id),
  label       TEXT NOT NULL CHECK (label IN ('BOT','HUMAN','SUSPICIOUS','UNSURE')),
  reason      TEXT,
  labeled_by  TEXT DEFAULT 'sasa',
  labeled_at  TIMESTAMPTZ DEFAULT NOW()
);
ALTER TABLE scanner_labels ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Public read" ON scanner_labels;
CREATE POLICY "Public read" ON scanner_labels FOR SELECT USING (true);

CREATE TABLE IF NOT EXISTS scanner_signal_lrs (
  signal_name    TEXT NOT NULL,
  version        INTEGER NOT NULL,
  lr_log_bot     DOUBLE PRECISION NOT NULL,
  lr_log_human   DOUBLE PRECISION NOT NULL,
  thresholds     JSONB,
  description    TEXT,
  calibrated_at  TIMESTAMPTZ DEFAULT NOW(),
  calibrated_by  TEXT DEFAULT 'expert-prior',
  active         BOOLEAN DEFAULT TRUE,
  PRIMARY KEY (signal_name, version)
);
CREATE INDEX IF NOT EXISTS scanner_signal_lrs_active
  ON scanner_signal_lrs(signal_name) WHERE active;
ALTER TABLE scanner_signal_lrs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Public read" ON scanner_signal_lrs;
CREATE POLICY "Public read" ON scanner_signal_lrs FOR SELECT USING (true);
