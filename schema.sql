-- Point-in-time research schema. NXT observations are never used for prior-close selection.
CREATE TABLE IF NOT EXISTS daily_signal_snapshot (
  trade_date DATE NOT NULL, symbol VARCHAR(12) NOT NULL, krx_close NUMERIC,
  legacy_score NUMERIC, structure_class VARCHAR(40), structure_score NUMERIC,
  cloud_position VARCHAR(20), cloud_gap_pct NUMERIC, bb_state VARCHAR(40),
  higher_low BOOLEAN, higher_high BOOLEAN, rsi14 NUMERIC, macd NUMERIC,
  volume_structure VARCHAR(40), raw_values_json TEXT, config_json TEXT,
  PRIMARY KEY (trade_date, symbol)
);
CREATE TABLE IF NOT EXISTS nxt_premarket_bar (
  trade_date DATE NOT NULL, symbol VARCHAR(12) NOT NULL, bar_time TIMESTAMP NOT NULL,
  open NUMERIC, high NUMERIC, low NUMERIC, close NUMERIC, volume NUMERIC,
  vwap NUMERIC, bucket_relative_volume NUMERIC,
  PRIMARY KEY (trade_date, symbol, bar_time)
);
CREATE TABLE IF NOT EXISTS nxt_premarket_summary (
  trade_date DATE NOT NULL, symbol VARCHAR(12) NOT NULL, prior_krx_close NUMERIC,
  nxt_open NUMERIC, nxt_high NUMERIC, nxt_low NUMERIC, nxt_last NUMERIC, nxt_volume NUMERIC,
  nxt_vwap NUMERIC, max_gap_pct NUMERIC, final_gap_pct NUMERIC, high_retention_pct NUMERIC,
  higher_low BOOLEAN, higher_high BOOLEAN, pattern VARCHAR(40), closing_strength NUMERIC,
  raw_values_json TEXT, PRIMARY KEY (trade_date, symbol)
);
CREATE TABLE IF NOT EXISTS krx_intraday_validation (
  trade_date DATE NOT NULL, symbol VARCHAR(12) NOT NULL, krx_open NUMERIC,
  open_gap_pct NUMERIC, price_0905 NUMERIC, price_0915 NUMERIC, price_0930 NUMERIC,
  day_high NUMERIC, day_low NUMERIC, day_close NUMERIC, nxt_to_krx_open_gap_pct NUMERIC,
  return_5m_pct NUMERIC, return_15m_pct NUMERIC, return_30m_pct NUMERIC,
  mfe_pct NUMERIC, mae_pct NUMERIC, close_return_pct NUMERIC,
  PRIMARY KEY (trade_date, symbol)
);
