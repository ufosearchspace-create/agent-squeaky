-- PR2 TTL cleanup: enable pg_cron and schedule a daily DELETE that keeps
-- only the last 30 days of 5m candles. Python-side _cleanup_old_candles
-- in candle_fetcher.py is still the primary safety net — this cron job
-- is a server-side backup in case the fetcher is paused for any reason.

CREATE EXTENSION IF NOT EXISTS pg_cron;

SELECT cron.schedule(
  'scanner_candles_ttl',
  '0 4 * * *',
  $$
  DELETE FROM public.scanner_candles
  WHERE ts_ms < (EXTRACT(EPOCH FROM (NOW() - INTERVAL '30 days')) * 1000)::bigint
  $$
);
