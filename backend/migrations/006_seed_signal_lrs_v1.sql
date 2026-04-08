-- Phase 1 seed: version 1 expert-prior likelihood ratios per signal.
-- Every signal is inserted with active=true and calibrated_by='expert-prior-v1'.
-- Subsequent calibration runs append version=2 rows and flip version=1 active
-- flags off. See docs/plans/2026-04-08-bayesian-scoring-redesign.md §6 for
-- the signal state tables these LRs correspond to.
--
-- Convention: thresholds JSON has a "states" object mapping state name to
-- log2-LR bits (positive = toward BOT, negative = toward HUMAN) and an
-- optional "cutoffs" object documenting the numeric boundaries signals use.

INSERT INTO scanner_signal_lrs
  (signal_name, version, lr_log_bot, lr_log_human, thresholds, description, calibrated_by, active)
VALUES
-- =====================================================================
-- TEMPORAL — T-series
-- =====================================================================
('T1_per_day_sleep_gap', 1, 3.0, -5.0,
 '{"states": {"strong_human": -5.0, "medium_human": -1.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0},
   "cutoffs": {"strong_human_median_h": 7, "strong_human_p70_days": 0.7,
               "medium_human_median_h": 6,
               "medium_bot_max_h": 4,
               "strong_bot_max_h": 2, "strong_bot_p70_days": 0.7}}',
 'Per-day median sleep gap (hours), measured per UTC day with wrap-around.',
 'expert-prior-v1', true),

('T2_sleep_window_stability', 1, 0.0, -3.0,
 '{"states": {"strong_human": -3.0, "medium_human": -1.5, "neutral": 0.0},
   "cutoffs": {"strong_human_stddev_h": 1.5, "medium_human_stddev_h": 3.0}}',
 'Circular stddev of sleep-midpoint UTC hour across days. Only runs when T1 is human-like.',
 'expert-prior-v1', true),

('T3_weekend_weekday_ratio', 1, 0.5, -1.5,
 '{"states": {"medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0, "weak_bot": 0.5},
   "cutoffs": {"medium_human_ratio_max": 0.5, "weak_human_ratio_max": 0.75, "weak_bot_ratio_min": 1.25}}',
 'Weekend average trades / weekday average trades. Requires >=7 days with both types.',
 'expert-prior-v1', true),

('T4_daily_volume_cv', 1, 3.0, -1.5,
 '{"states": {"medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0},
   "cutoffs": {"medium_human_cv_min": 1.0, "weak_human_cv_min": 0.6,
               "medium_bot_cv_max": 0.3, "strong_bot_cv_max": 0.15}}',
 'Coefficient of variation of per-day trade counts.',
 'expert-prior-v1', true),

('T5_dead_days', 1, 1.5, -1.5,
 '{"states": {"medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5},
   "cutoffs": {"medium_human_dead_min": 2, "weak_human_dead_min": 1, "medium_bot_min_active_days": 14}}',
 'Number of days inside the active window with zero trades.',
 'expert-prior-v1', true),

('T6_intraday_burst_score', 1, 1.5, 0.0,
 '{"states": {"neutral": 0.0, "weak_bot": 0.5, "medium_bot": 1.5},
   "cutoffs": {"weak_bot_burst_pct": 0.4, "medium_bot_burst_pct": 0.6}}',
 'Percentage of trades in 5-minute 3+ trade clusters.',
 'expert-prior-v1', true),

('T7_per_day_interval_cv', 1, 0.5, 0.0,
 '{"states": {"neutral": 0.0, "weak_bot": 0.5},
   "cutoffs": {"weak_bot_median_cv_max": 0.3}}',
 'Median of per-day inter-trade interval CV.',
 'expert-prior-v1', true),

('T8_ms_entropy', 1, 3.0, 0.0,
 '{"states": {"neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0},
   "cutoffs": {"strong_bot_zero_ms_pct": 0.8, "medium_bot_entropy_max_bits": 3.0, "neutral_entropy_min_bits": 5.0}}',
 'Entropy of (closed_at_ms MOD 1000) distribution. Legacy bots trade on exact seconds.',
 'expert-prior-v1', true),

-- =====================================================================
-- STRUCTURAL — S-series
-- =====================================================================
('S1_round_size_pct', 1, 0.5, -1.5,
 '{"states": {"medium_human": -1.5, "weak_human": -0.5, "neutral": 0.0, "weak_bot": 0.5},
   "cutoffs": {"medium_human_pct_min": 0.4, "weak_human_pct_min": 0.2, "weak_bot_pct_max": 0.05}}',
 'Percentage of position_size values divisible by {10,25,50,100,250,500} or simple integers <=1000.',
 'expert-prior-v1', true),

('S2_size_decimal_precision', 1, 3.0, 0.0,
 '{"states": {"neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0},
   "cutoffs": {"medium_bot_avg_decimals_min": 4, "strong_bot_avg_decimals_min": 5}}',
 'Average number of decimal places in position_size.',
 'expert-prior-v1', true),

('S3_benford_compliance', 1, 1.5, -0.5,
 '{"states": {"weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5},
   "cutoffs": {"weak_human_p_min": 0.3, "medium_bot_p_max": 0.05}}',
 'Benford leading-digit chi-square test p-value on position_size.',
 'expert-prior-v1', true),

('S4_coin_diversity', 1, 3.0, -0.5,
 '{"states": {"weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0},
   "cutoffs": {"strong_bot_unique_min": 15, "strong_bot_ratio_min": 0.2,
               "medium_bot_unique_min": 10,
               "weak_human_unique_max": 3, "weak_human_trade_min": 20}}',
 'Unique coins traded (absolute + ratio against trade count).',
 'expert-prior-v1', true),

('S5_size_ladder_pattern', 1, 3.0, 0.0,
 '{"states": {"neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0},
   "cutoffs": {"strong_bot_top3_coverage": 0.7}}',
 'Position sizes cluster into <=3 buckets covering >70% of trades (fixed ladder).',
 'expert-prior-v1', true),

('S6_identical_size_repetition', 1, 3.0, -0.5,
 '{"states": {"weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0},
   "cutoffs": {"strong_bot_max_freq": 0.5, "medium_bot_max_freq": 0.3,
               "weak_human_unique_ratio_min": 0.95, "weak_human_trade_min": 30}}',
 'Max frequency of any single position_size value.',
 'expert-prior-v1', true),

('S7_leverage_variance', 1, 1.5, -0.5,
 '{"states": {"weak_human": -0.5, "neutral": 0.0, "weak_bot": 0.5, "medium_bot": 1.5},
   "cutoffs": {"medium_bot_distinct_max": 1,
               "weak_bot_distinct_max": 2, "weak_bot_dominant_min": 0.9,
               "weak_human_distinct_min": 4, "weak_human_entropy_min_bits": 1.5}}',
 'Number of distinct leverage values plus entropy of their distribution.',
 'expert-prior-v1', true),

-- =====================================================================
-- META — M-series (Phase 1 slice only; M1-M4, M6 land in PR3)
-- =====================================================================
('M5_cross_agent_consistency', 1, 0.0, -0.5,
 '{"states": {"neutral": 0.0, "weak_human": -0.5},
   "cutoffs": {"weak_human_fingerprint_distance_min": 0.4}}',
 'L1 distance of this agent behavioral fingerprint from the cluster centroid across siblings of the same owner.',
 'expert-prior-v1', true),

-- =====================================================================
-- BEHAVIORAL — B-series (Phase 2; available immediately after Phase 0 refetch)
-- =====================================================================
('B1_hold_time_variance', 1, 3.0, -1.5,
 '{"states": {"medium_human": -1.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0},
   "cutoffs": {"medium_human_log_cv_min": 1.2,
               "medium_bot_log_cv_max": 0.3,
               "strong_bot_bimodal_cluster_share": 0.9}}',
 'CV of log(hold_time_s). Bimodal clustering is a stronger bot indicator than uniform tightness.',
 'expert-prior-v1', true),

('B2_hold_time_median', 1, 0.5, 0.0,
 '{"states": {"neutral": 0.0, "weak_bot": 0.5},
   "cutoffs": {"weak_bot_scalper_s": 60, "weak_bot_swing_s": 86400}}',
 'Median hold_time_s. Extreme scalper or long-swing fixed.',
 'expert-prior-v1', true),

('B3_win_loss_hold_asymmetry', 1, 1.5, -3.0,
 '{"states": {"strong_human": -3.0, "medium_human": -1.5, "neutral": 0.0, "weak_bot": 0.5, "medium_bot": 1.5},
   "cutoffs": {"strong_human_ratio_min": 2.0, "medium_human_ratio_min": 1.5,
               "medium_bot_ratio_lo": 0.9, "medium_bot_ratio_hi": 1.1,
               "weak_bot_ratio_max": 0.7,
               "min_wins": 10, "min_losses": 10}}',
 'median(hold_time_losses) / median(hold_time_wins). Humans hold losers longer (emotional bias).',
 'expert-prior-v1', true),

('B5_concurrent_open_positions', 1, 3.0, -0.5,
 '{"states": {"weak_human": -0.5, "neutral": 0.0, "medium_bot": 1.5, "strong_bot": 3.0},
   "cutoffs": {"strong_bot_max_min": 10, "strong_bot_median_min": 5,
               "medium_bot_max_min": 6, "medium_bot_median_min": 3,
               "weak_human_max_max": 2}}',
 'Max and median concurrent open positions derived from overlapping opened_at / closed_at windows.',
 'expert-prior-v1', true)
ON CONFLICT (signal_name, version) DO NOTHING;
