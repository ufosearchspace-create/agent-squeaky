-- Phase 0: scanner_agent_latest_scores view rewrite
-- Replaces the old D-column view with the Bayesian posterior fields and
-- joins owner_cluster_count + onchain owner profile so the frontend can
-- render the full evidence story without extra queries.

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
