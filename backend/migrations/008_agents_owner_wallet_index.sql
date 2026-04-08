-- Database review follow-up: scanner_agent_latest_scores view contains a
-- correlated subquery that counts agents per owner_wallet on every row
-- read. Without an index on scanner_agents.owner_wallet the count becomes
-- a sequential scan once the view is the dashboard's hot path. Cheap
-- defensive index — plain btree is enough, the column is low cardinality
-- but highly selective for this query.

CREATE INDEX IF NOT EXISTS idx_scanner_agents_owner_wallet
  ON scanner_agents(owner_wallet)
  WHERE owner_wallet IS NOT NULL;
