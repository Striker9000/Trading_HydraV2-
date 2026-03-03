# Changelog

## [Unreleased] — Advanced Indicators (Tasks 5–10)

### Added

#### `src/trading_hydra/indicators/fair_value_gap.py` *(new)*
- `FairValueGapDetector` class: detects 3-candle imbalance zones (bullish/bearish FVGs)
- `detect_fvgs(bars)` — scans bars for Fair Value Gaps
- `get_nearest_fvg(price, direction, bars)` — finds closest relevant FVG within 0.5% proximity

#### `src/trading_hydra/indicators/vwap_posture.py`
- `VWAPPostureManager.compute_volume_profile(bars, bins=20)` — POC, VAH, VAL via 70% value area
- `VWAPPostureManager.compute_anchored_vwap(intraday_bars, anchor_price)` — prior-day-close-anchored VWAP
- Module-level `detect_liquidity_sweep(bars, lookback=10)` — wick-beyond-swing + close-back reversal detection
- Module-level `compute_order_flow(bars)` — bull/bear volume delta approximation
- `PostureDecision` gains: `poc`, `vah`, `val`, `anchored_vwap`, `price_above_avwap`
- `PostureDecision.to_dict()` includes all new fields

#### `src/trading_hydra/bots/twenty_minute_bot.py`
- `VWAPMomentumIndicators` gains 13 new fields:
  - Sigma bands: `vwap_upper_1sigma`, `vwap_lower_1sigma`, `vwap_upper_2sigma`, `vwap_lower_2sigma`
  - Volume profile: `vwap_poc`, `vwap_vah`, `vwap_val`
  - Anchored VWAP: `anchored_vwap`, `price_above_avwap`
  - FVG flags: `fvg_bullish_nearby`, `fvg_bearish_nearby`
  - Sweep flags: `liquidity_sweep_up`, `liquidity_sweep_down`
  - Order flow: `order_flow_delta`, `order_flow_bullish`
- `_compute_momentum_indicators()` extracts all new values from `PostureDecision` + inline FVG/sweep/flow
- `_validate_with_momentum()`:
  - **Order flow gate** — rejects long if bearish flow, rejects short if bullish flow
  - **Sigma band target override** — replaces % target with VWAP 1σ band when it's a closer level
- All new fields logged in `twentymin_momentum_indicators` events

*Mirror copies in `trading_hydra/` updated identically.*
