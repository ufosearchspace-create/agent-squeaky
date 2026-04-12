-- PR4 schema: add human_assisted BOOLEAN to scanner_scores and expose
-- it on the scanner_agent_latest_scores view so the frontend can render
-- the HUMAN_ASSISTED plaque without a second query.
--
-- human_assisted is written by scoring_engine.classifier.evaluate_human_assisted
-- during the per-agent analyzer cycle. It is a parallel flag (not a
-- replacement for classification) — an agent can simultaneously be
-- BOT + HUMAN_ASSISTED, meaning the posterior still calls it a bot but
-- at least two psychology signals converge on "human intervention".
--
-- Default FALSE so old rows keep working without backfill — the next
-- analyzer run will populate accurate values from the current trade
-- history. No DROP DEFAULT later: FALSE is the correct "not enough
-- evidence" interpretation for historical rows.

ALTER TABLE scanner_scores
  ADD COLUMN IF NOT EXISTS human_assisted BOOLEAN NOT NULL DEFAULT FALSE;

-- Rewrite the view so the column is selectable by the frontend. Keeping
-- the LATERAL JOIN pattern identical to migration 004 — only the
-- column list changes.

DROP VIEW IF EXISTS scanner_agent_latest_scores;

CREATE VIEW scanner_agent_latest_scores
WITH (security_invoker = false)
AS
SELECT
  a.id,
  a.name,
  a.wallet_address,
  a.owner_wallet,
  a.total_pnl,
  a.trade_count,
  a.win_rate,
  s.prior_log_odds,
  s.posterior_log_odds,
  s.p_bot,
  s.evidence_log,
  s.hard_gates_hit,
  s.classification,
  s.human_assisted,
  s.lr_version,
  s.scored_at,
  s.trade_count_at_scoring,
  (SELECT COUNT(*)
   FROM scanner_agents o
   WHERE o.owner_wallet IS NOT NULL
     AND o.owner_wallet = a.owner_wallet) AS owner_agents_count,
  oc.age_days          AS owner_age_days,
  oc.activity_score    AS owner_activity_score,
  oc.chains_active     AS owner_chains_active
FROM scanner_agents a
LEFT JOIN LATERAL (
  SELECT *
  FROM scanner_scores sc
  WHERE sc.agent_id = a.id
  ORDER BY sc.scored_at DESC
  LIMIT 1
) s ON true
LEFT JOIN scanner_onchain oc ON oc.owner_wallet = a.owner_wallet
ORDER BY s.p_bot DESC NULLS LAST, a.trade_count DESC;
