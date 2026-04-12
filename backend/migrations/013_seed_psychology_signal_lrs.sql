-- PR4 seed: version 1 expert-prior likelihood ratios for the eight
-- psychology signals. Every signal is inserted with active=true and
-- calibrated_by='expert-prior-v2' (v2 namespace so the psychology LRs
-- can be re-tuned independently of the original 26-signal seed).
--
-- Convention matches migration 006: thresholds JSON has a "states"
-- object mapping state name to log2-LR bits (positive = toward BOT,
-- negative = toward HUMAN).
--
-- Academic sources for each signal are recorded in the description
-- column so calibration audits can trace back why each threshold was
-- chosen. See backend/scoring_engine/signals/psychology.py for the
-- signal implementations and state-transition logic.

INSERT INTO scanner_signal_lrs
  (signal_name, version, lr_log_bot, lr_log_human, thresholds, description, calibrated_by, active)
VALUES
-- =====================================================================
-- B6 disposition_effect — Shefrin & Statman 1985; Odean 1998
-- =====================================================================
('B6_disposition_effect', 1, 0.3, -1.5,
 '{"states": {"strong_human": -1.5, "medium_human": -0.7, "neutral": 0.0, "weak_bot": 0.3},
   "cutoffs": {"strong_human_ratio_min": 2.0, "medium_human_ratio_min": 1.3,
               "neutral_ratio_min": 0.8, "neutral_ratio_max": 1.3,
               "weak_bot_ratio_max": 0.5}}',
 'Disposition effect (Shefrin & Statman 1985): median(loss_hold)/median(win_hold). Humans hold losers longer than winners; bots produce a flat ratio near 1.0.',
 'expert-prior-v2', true),

-- =====================================================================
-- B7 loss_chase_sizing — Kahneman & Tversky 1979, prospect theory
-- =====================================================================
('B7_loss_chase_sizing', 1, 0.0, -1.5,
 '{"states": {"strong_human": -1.5, "medium_human": -0.8, "weak_human": -0.3, "neutral": 0.0, "medium_bot": 0.5},
   "cutoffs": {"strong_human_r_max": -0.30, "medium_human_r_max": -0.15,
               "weak_human_r_min": 0.15,
               "medium_bot_abs_r_max": 0.05}}',
 'Loss-chase sizing (Kahneman & Tversky 1979): Pearson r(rolling_pnl_5, next_size). Negative r means sizes escalate after losing streaks — classic chase. Zero r means mechanical sizing.',
 'expert-prior-v2', true),

-- =====================================================================
-- B8 hot_hand_tempo — Gilovich, Vallone, Tversky 1985
-- =====================================================================
('B8_hot_hand_tempo', 1, 0.0, -1.3,
 '{"states": {"strong_human": -1.3, "medium_human": -0.7, "neutral": 0.0, "medium_bot": 0.4},
   "cutoffs": {"strong_human_r_min": 0.35, "medium_human_r_min": 0.15,
               "medium_bot_abs_r_max": 0.10}}',
 'Hot-hand tempo (Gilovich et al. 1985): Pearson r(trailing_win_rate, next_window_pace). Humans accelerate after win streaks; bots maintain constant tempo.',
 'expert-prior-v2', true),

-- =====================================================================
-- B9 tilt_spike — Lo, Repin, Steenbarger 2005
-- =====================================================================
('B9_tilt_spike', 1, 0.0, -1.4,
 '{"states": {"strong_human": -1.4, "medium_human": -0.8, "neutral": 0.0, "medium_bot": 0.3},
   "cutoffs": {"strong_human_ratio_min": 2.0,
               "medium_human_ratio_min": 1.3,
               "medium_human_ratio_max_freeze": 0.4,
               "medium_bot_ratio_min": 0.8, "medium_bot_ratio_max": 1.2}}',
 'Tilt spike (Lo/Repin/Steenbarger 2005): trade-rate ratio in the 30-min window after a 1σ loss vs baseline. Humans revenge-trade (>>1) or fear-freeze (<<1); bots maintain baseline.',
 'expert-prior-v2', true),

-- =====================================================================
-- S8 round_pnl_exits — Harris 1991; Bhattacharya et al. 2012
-- =====================================================================
('S8_round_pnl_exits', 1, 0.3, -1.2,
 '{"states": {"strong_human": -1.2, "medium_human": -0.6, "neutral": 0.0, "medium_bot": 0.3},
   "cutoffs": {"strong_human_pct_min": 0.55, "medium_human_pct_min": 0.42,
               "medium_bot_pct_max": 0.20,
               "tolerance_pct": 0.05}}',
 'Round-number PnL exits (Harris 1991; Bhattacharya et al. 2012): fraction of closed_pnl values within ±5% of a round multiple (10/25/50/100/250/500). Humans take profits on round numbers; bots exit on algorithmic levels.',
 'expert-prior-v2', true),

-- =====================================================================
-- S9 anchor_exits — Tversky & Kahneman 1974
-- =====================================================================
('S9_anchor_exits', 1, 0.3, -1.4,
 '{"states": {"strong_human": -1.4, "medium_human": -0.7, "neutral": 0.0, "medium_bot": 0.3},
   "cutoffs": {"strong_human_pct_min": 0.45, "medium_human_pct_min": 0.30,
               "medium_bot_pct_max": 0.12,
               "tolerance_abs": 0.0015,
               "anchor_percents": [0.01, 0.02, 0.025, 0.05, 0.075, 0.10, 0.15, 0.20]}}',
 'Anchor-price exits (Tversky & Kahneman 1974): fraction of (exit-entry)/entry returns within 0.15pp of a round percentage (±1%, ±2%, ±5%, ±10%, ±20%). Humans anchor on entry price.',
 'expert-prior-v2', true),

-- =====================================================================
-- T9 gap_entropy — circadian rhythms research
-- =====================================================================
('T9_gap_entropy', 1, 0.3, -0.9,
 '{"states": {"medium_human": -0.9, "weak_human": -0.4, "neutral": 0.0, "no_gaps": 0.3},
   "cutoffs": {"medium_human_entropy_max": 1.5,
               "weak_human_entropy_min_chaos": 3.0,
               "min_gap_hours": 2, "min_gap_count": 5,
               "min_span_days": 7}}',
 'Gap entropy (Czeisler et al. circadian research): Shannon entropy of hours at which >=2h trade gaps begin. Low entropy = tight circadian cluster (human), chaotic high entropy = hybrid marker, no gaps = bot-like 24/7.',
 'expert-prior-v2', true),

-- =====================================================================
-- B10 intraday_emotion_shape — retail U-shape research (Lopez de Prado)
-- =====================================================================
('B10_intraday_emotion_shape', 1, 1.2, -0.8,
 '{"states": {"medium_human": -0.8, "weak_human": -0.3, "neutral": 0.0, "medium_bot": 0.4, "strong_bot": 1.2},
   "cutoffs": {"medium_bot_cv_max": 0.30, "strong_bot_cv_min_spike": 1.20,
               "medium_human_u_ratio_min": 2.0, "medium_human_cv_min": 0.50,
               "weak_human_cv_min": 0.40, "weak_human_cv_max": 0.80}}',
 'Intraday emotion shape (retail U-shape, Lopez de Prado 2018): coefficient-of-variation of 24-hour trade distribution + U-shape ratio (open+close / mid-day). Retail humans cluster at market open and close.',
 'expert-prior-v2', true);
