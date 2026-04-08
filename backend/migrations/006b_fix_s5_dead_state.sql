-- CEK audit finding H1: S5_size_ladder_pattern seed row includes a
-- "medium_bot" state that the signal module never produces. The code only
-- maps to "strong_bot" when the top-3 bucket share exceeds 0.7, and to
-- "neutral" otherwise. Leaving the dead state in the LR cache silently
-- wastes a row and confuses calibration diffs.
--
-- Rewrite only the states sub-object on the v1 row, leaving cutoffs and
-- everything else intact.

UPDATE scanner_signal_lrs
SET thresholds = jsonb_set(
      thresholds,
      '{states}',
      '{"neutral": 0.0, "strong_bot": 3.0}'::jsonb
    )
WHERE signal_name = 'S5_size_ladder_pattern'
  AND version = 1;
