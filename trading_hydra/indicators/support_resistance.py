"""
=============================================================================
Support / Resistance Level Detector
=============================================================================
Identifies historical price levels where reversals have occurred, using
swing-high / swing-low detection with volume weighting and level clustering.

Methods
-------
- Swing Point Detection: Identifies local peaks (resistance) and troughs
  (support) using a configurable lookback window.
- Volume Weighting: Levels formed on high-volume bars carry more weight,
  since institutional players are more likely to have established positions
  there.
- Level Clustering: Multiple nearby swing points are merged into zones
  to avoid noise.  The cluster radius is configurable.
- Touch Counting: Levels that have been tested multiple times without
  breaking are stronger.  A "touch" is when price comes within a
  configurable proximity without closing beyond the level.
- Recency Decay: Older levels are weighted less than recent ones.

Public API
----------
    detect_levels(bars, config) -> List[SRLevel]
        Returns ranked support and resistance levels.

    find_nearest_support(price, levels) -> Optional[SRLevel]
    find_nearest_resistance(price, levels) -> Optional[SRLevel]
        Convenience helpers for the simulation engine.

    price_near_sr(price, levels, proximity_pct) -> dict
        Returns info about whether price is near a S/R zone.
=============================================================================
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Tuple


@dataclass
class SRLevel:
    price: float
    level_type: str        # "support" or "resistance"
    strength: float        # 0-100 composite score
    touch_count: int       # how many times price tested this level
    volume_weight: float   # avg volume at formation relative to mean
    first_bar_idx: int     # when level first appeared
    last_touch_idx: int    # most recent test
    cluster_size: int      # how many raw swing points merged into this

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _find_swing_highs(bars: List[Dict], lookback: int) -> List[Tuple[int, float, float]]:
    """
    Detect local peaks.  A bar at index i is a swing high if its high is
    the maximum high in [i - lookback, i + lookback].
    Returns list of (bar_index, price, volume).
    """
    results = []
    n = len(bars)
    for i in range(lookback, n - lookback):
        candidate = bars[i]["high"]
        is_peak = True
        for j in range(i - lookback, i + lookback + 1):
            if j == i:
                continue
            if 0 <= j < n and bars[j]["high"] > candidate:
                is_peak = False
                break
        if is_peak:
            results.append((i, candidate, bars[i].get("volume", 0)))
    return results


def _find_swing_lows(bars: List[Dict], lookback: int) -> List[Tuple[int, float, float]]:
    """
    Detect local troughs.  A bar at index i is a swing low if its low is
    the minimum low in [i - lookback, i + lookback].
    Returns list of (bar_index, price, volume).
    """
    results = []
    n = len(bars)
    for i in range(lookback, n - lookback):
        candidate = bars[i]["low"]
        is_trough = True
        for j in range(i - lookback, i + lookback + 1):
            if j == i:
                continue
            if 0 <= j < n and bars[j]["low"] < candidate:
                is_trough = False
                break
        if is_trough:
            results.append((i, candidate, bars[i].get("volume", 0)))
    return results


def _cluster_levels(
    raw_points: List[Tuple[int, float, float]],
    cluster_pct: float,
) -> List[Dict]:
    """
    Merge nearby swing points into clusters.
    cluster_pct: maximum percentage distance between points to merge
                 (e.g. 0.3 means 0.3 % of price).
    Returns list of cluster dicts with volume-weighted price, count, etc.
    """
    if not raw_points:
        return []

    sorted_pts = sorted(raw_points, key=lambda x: x[1])
    clusters: List[Dict] = []
    current_cluster = {
        "prices": [sorted_pts[0][1]],
        "volumes": [sorted_pts[0][2]],
        "indices": [sorted_pts[0][0]],
    }

    for idx, price, vol in sorted_pts[1:]:
        centre = statistics.mean(current_cluster["prices"])
        if centre > 0 and abs(price - centre) / centre * 100 <= cluster_pct:
            current_cluster["prices"].append(price)
            current_cluster["volumes"].append(vol)
            current_cluster["indices"].append(idx)
        else:
            clusters.append(current_cluster)
            current_cluster = {
                "prices": [price],
                "volumes": [vol],
                "indices": [idx],
            }

    clusters.append(current_cluster)
    return clusters


def _count_touches(
    bars: List[Dict],
    level_price: float,
    proximity_pct: float,
    start_idx: int = 0,
) -> Tuple[int, int]:
    """
    Count how many bars had their intrabar range (high/low) overlap the
    level's proximity band (price ± proximity_pct%).  This captures wicks
    that tested the level.  Returns (touch_count, last_touch_idx).
    """
    touches = 0
    last_touch = start_idx
    band = level_price * proximity_pct / 100

    for i in range(start_idx, len(bars)):
        bar_low = bars[i]["low"]
        bar_high = bars[i]["high"]
        if bar_low <= level_price + band and bar_high >= level_price - band:
            touches += 1
            last_touch = i

    return touches, last_touch


def detect_levels(
    bars: List[Dict],
    config: Optional[Dict] = None,
) -> List[SRLevel]:
    """
    Main entry point.  Analyses a list of OHLCV bar dicts and returns
    ranked support / resistance levels.

    Config keys (with defaults):
        sr_lookback          (5)    – swing detection window size (bars each side)
        sr_cluster_pct       (0.3)  – max % distance to merge swing points
        sr_touch_proximity   (0.2)  – % band for counting touches
        sr_min_touches       (2)    – minimum touches to keep a level
        sr_recency_halflife  (50)   – bars for recency decay half-life
        sr_min_strength      (10)   – minimum composite strength to keep
    """
    if config is None:
        config = {}

    lookback = int(config.get("sr_lookback", 5))
    cluster_pct = float(config.get("sr_cluster_pct", 0.3))
    touch_prox = float(config.get("sr_touch_proximity", 0.2))
    min_touches = int(config.get("sr_min_touches", 2))
    recency_hl = float(config.get("sr_recency_halflife", 50))
    min_strength = float(config.get("sr_min_strength", 10))

    if len(bars) < lookback * 2 + 1:
        return []

    avg_vol = statistics.mean([b.get("volume", 1) for b in bars]) if bars else 1
    if avg_vol <= 0:
        avg_vol = 1
    total_bars = len(bars)

    swing_highs = _find_swing_highs(bars, lookback)
    swing_lows = _find_swing_lows(bars, lookback)

    levels: List[SRLevel] = []

    for level_type, raw_points in [("resistance", swing_highs), ("support", swing_lows)]:
        clusters = _cluster_levels(raw_points, cluster_pct)

        for cl in clusters:
            total_vol = sum(cl["volumes"])
            total_count = len(cl["prices"])
            if total_vol > 0 and total_count > 0:
                vw_price = sum(p * v for p, v in zip(cl["prices"], cl["volumes"])) / total_vol
            else:
                vw_price = statistics.mean(cl["prices"])

            vol_weight = (statistics.mean(cl["volumes"]) / avg_vol) if avg_vol > 0 else 1.0
            first_idx = min(cl["indices"])
            last_idx = max(cl["indices"])

            touch_count, last_touch = _count_touches(bars, vw_price, touch_prox, first_idx)
            if touch_count < min_touches:
                continue

            import math
            bars_since_last = total_bars - last_touch
            recency_factor = math.exp(-0.693 * bars_since_last / recency_hl) if recency_hl > 0 else 1.0

            strength = (
                min(touch_count, 10) * 10          # touches: up to 100
                + min(vol_weight, 3.0) * 15        # volume: up to 45
                + total_count * 5                  # cluster size: uncapped
            ) * recency_factor

            if strength < min_strength:
                continue

            levels.append(SRLevel(
                price=round(vw_price, 4),
                level_type=level_type,
                strength=round(strength, 2),
                touch_count=touch_count,
                volume_weight=round(vol_weight, 3),
                first_bar_idx=first_idx,
                last_touch_idx=last_touch,
                cluster_size=total_count,
            ))

    levels.sort(key=lambda lv: lv.strength, reverse=True)
    return levels


def find_nearest_support(price: float, levels: List[SRLevel]) -> Optional[SRLevel]:
    """Return the closest support level below the current price."""
    supports = [lv for lv in levels if lv.level_type == "support" and lv.price < price]
    if not supports:
        return None
    return min(supports, key=lambda lv: price - lv.price)


def find_nearest_resistance(price: float, levels: List[SRLevel]) -> Optional[SRLevel]:
    """Return the closest resistance level above the current price."""
    resistances = [lv for lv in levels if lv.level_type == "resistance" and lv.price > price]
    if not resistances:
        return None
    return min(resistances, key=lambda lv: lv.price - price)


def price_near_sr(
    price: float,
    levels: List[SRLevel],
    proximity_pct: float = 0.3,
) -> Dict[str, Any]:
    """
    Check whether the current price is close to any support or resistance
    level.  Returns a dict with:
        near_support: bool
        near_resistance: bool
        nearest_support: Optional[SRLevel]
        nearest_resistance: Optional[SRLevel]
        support_distance_pct: float  (always positive, 999 if none)
        resistance_distance_pct: float
        support_strength: float  (0 if none)
        resistance_strength: float
    """
    ns = find_nearest_support(price, levels)
    nr = find_nearest_resistance(price, levels)

    s_dist = ((price - ns.price) / price * 100) if ns else 999.0
    r_dist = ((nr.price - price) / price * 100) if nr else 999.0

    return {
        "near_support": s_dist <= proximity_pct,
        "near_resistance": r_dist <= proximity_pct,
        "nearest_support": ns,
        "nearest_resistance": nr,
        "support_distance_pct": round(s_dist, 4),
        "resistance_distance_pct": round(r_dist, 4),
        "support_strength": ns.strength if ns else 0,
        "resistance_strength": nr.strength if nr else 0,
    }


def compute_sr_levels_for_day(
    prev_bars: List[Dict],
    config: Optional[Dict] = None,
) -> List[SRLevel]:
    """
    Convenience wrapper: compute S/R levels from the previous N days of bars
    (the "lookback history") that will be used during today's simulation.
    This should be called once per symbol-day, not per bar.
    """
    return detect_levels(prev_bars, config)
