-- PR3 seed: expert-prior v1 LRs for M1, M2, M3, M6 onchain signals.
-- States must match scoring_engine/signals/onchain.py exactly.
-- Cutoffs are documentation only; the signal module hard-codes them.

INSERT INTO scanner_signal_lrs
  (signal_name, version, lr_log_bot, lr_log_human, thresholds, description, calibrated_by, active)
VALUES
('M1_owner_wallet_age', 1, 0.5, -1.5,
 '{"states": {"medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0, "weak_bot": 0.5}, "cutoffs": {"medium_human_age_days": 365, "weak_human_age_days": 180, "weak_bot_age_days_max": 14}}',
 'Age of owner wallet first Base transaction in days.',
 'expert-prior-v1', true),
('M2_owner_multi_chain', 1, 0.0, -1.5,
 '{"states": {"medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0}, "cutoffs": {"medium_human_chains_min": 5, "weak_human_chains_min": 3}}',
 'Number of EVM chains the owner wallet has activity on.',
 'expert-prior-v1', true),
('M3_owner_activity_score', 1, 0.0, -1.5,
 '{"states": {"medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0}, "cutoffs": {"medium_human_log10_min": 2.5, "weak_human_log10_min": 1.5}}',
 'log10(total_tx_count) on the owner wallet.',
 'expert-prior-v1', true),
('M6_throwaway_owner_flag', 1, 1.5, 0.0,
 '{"states": {"neutral": 0.0, "weak_bot": 0.5, "medium_bot": 1.5}, "cutoffs": {"medium_bot_age_max_days": 14, "medium_bot_tx_max": 10, "weak_bot_age_max_days": 30, "weak_bot_tx_max": 20}}',
 'Young active owner wallet likely throwaway for agent creation.',
 'expert-prior-v1', true)
ON CONFLICT (signal_name, version) DO NOTHING;
