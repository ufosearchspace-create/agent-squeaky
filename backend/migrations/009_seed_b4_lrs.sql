-- PR2 seed: expert-prior LRs for B4 (price reaction lag) and B4b
-- (pre-spike entry rate). Both operate on 5m candle buckets so the max
-- bit ceiling is intentionally lower than a sub-second sampler would
-- justify — strong_bot caps at +3.0 rather than +5.0.
--
-- States must match scoring_engine/signals/reaction.py exactly.
-- Cutoffs are documentation only, the signal module hard-codes them.

INSERT INTO scanner_signal_lrs
  (signal_name, version, lr_log_bot, lr_log_human, thresholds, description, calibrated_by, active)
VALUES
('B4_price_reaction_lag', 1, 3.0, -0.5,
 '{"states": {"weak_human": -0.5, "neutral": 0.0, "weak_bot": 0.5, "medium_bot": 1.5, "strong_bot": 3.0}, "cutoffs": {"bucket_ms": 300000, "max_lag_buckets": 3, "spike_threshold_pct": 0.5, "min_samples": 10, "strong_bot_same_bucket_pct": 0.7, "medium_bot_same_bucket_pct": 0.5, "weak_bot_within_one_pct": 0.6, "weak_human_lag_cv_min": 1.0}}',
 'Reaction lag from 5m price spike to trade open, bucketed in 5m candles.',
 'expert-prior-v1', true),
('B4b_pre_spike_entry_rate', 1, 0.5, 0.0,
 '{"states": {"neutral": 0.0, "weak_bot": 0.5}, "cutoffs": {"weak_bot_rate_min": 0.3, "min_samples": 10}}',
 'Fraction of spike-proximate trades that opened BEFORE the spike.',
 'expert-prior-v1', true)
ON CONFLICT (signal_name, version) DO NOTHING;
