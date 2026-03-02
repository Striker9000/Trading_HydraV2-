"""
Fair Value Gap (FVG) Detection
==============================
Detects 3-candle imbalance zones (Fair Value Gaps) in price action.

A Fair Value Gap is created when a candle moves so strongly that it leaves
an unfilled gap between the prior candle's extreme and the following candle's
extreme. These zones act as strong support (bullish FVG) or resistance
(bearish FVG) when price returns to test them.

Bullish FVG: bars[i-2].high < bars[i].low
    — gap between top of two-candles-ago and bottom of current candle
Bearish FVG: bars[i-2].low > bars[i].high
    — gap between bottom of two-candles-ago and top of current candle

FVGs are "mitigated" (filled) once price trades back into the zone.
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field


@dataclass
class FVG:
    """
    A Fair Value Gap zone.

    Attributes:
        top: Upper boundary of the gap zone
        bottom: Lower boundary of the gap zone
        is_bullish: True = bullish FVG (support), False = bearish FVG (resistance)
        mitigated: True once price has traded back into the zone
    """
    top: float
    bottom: float
    is_bullish: bool
    mitigated: bool = False

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def size(self) -> float:
        return self.top - self.bottom


class FairValueGapDetector:
    """
    Detects Fair Value Gaps (3-candle imbalance zones).

    Usage:
        detector = FairValueGapDetector()
        fvgs = detector.detect_fvgs(bars)
        nearest = detector.get_nearest_fvg(price, "long", bars)
    """

    def detect_fvgs(self, bars: List[Dict]) -> List[FVG]:
        """
        Find all FVGs in the provided bars.

        Args:
            bars: List of OHLCV dicts with keys high/h, low/l, close/c

        Returns:
            List of FVG instances (unmitigated and mitigated)
        """
        fvgs: List[FVG] = []
        if not bars or len(bars) < 3:
            return fvgs

        for i in range(2, len(bars)):
            high_prev2 = float(bars[i - 2].get("high", bars[i - 2].get("h", 0)))
            low_prev2 = float(bars[i - 2].get("low", bars[i - 2].get("l", 0)))
            low_curr = float(bars[i].get("low", bars[i].get("l", 0)))
            high_curr = float(bars[i].get("high", bars[i].get("h", 0)))

            # Bullish FVG: gap between top of [i-2] and bottom of [i]
            if high_prev2 > 0 and low_curr > 0 and high_prev2 < low_curr:
                fvgs.append(FVG(top=low_curr, bottom=high_prev2, is_bullish=True))

            # Bearish FVG: gap between bottom of [i-2] and top of [i]
            elif low_prev2 > 0 and high_curr > 0 and low_prev2 > high_curr:
                fvgs.append(FVG(top=low_prev2, bottom=high_curr, is_bullish=False))

        return fvgs

    def get_nearest_fvg(
        self, price: float, direction: str, bars: List[Dict], proximity_pct: float = 0.005
    ) -> Optional[FVG]:
        """
        Get the closest relevant FVG for the given price and trade direction.

        For "long": looks for bullish FVGs (support) below current price.
        For "short": looks for bearish FVGs (resistance) above current price.

        Args:
            price: Current market price
            direction: "long" or "short"
            bars: OHLCV bar dicts used to detect FVGs
            proximity_pct: Only return FVGs within this fraction of price (default 0.5%)

        Returns:
            Nearest relevant FVG, or None if none found within proximity
        """
        fvgs = self.detect_fvgs(bars)
        if not fvgs or price <= 0:
            return None

        if direction == "long":
            # Bullish FVGs below current price (act as support)
            candidates = [f for f in fvgs if f.is_bullish and f.top < price]
            if not candidates:
                return None
            nearest = max(candidates, key=lambda f: f.top)
            if abs(price - nearest.top) / price > proximity_pct:
                return None
            return nearest

        else:
            # Bearish FVGs above current price (act as resistance)
            candidates = [f for f in fvgs if not f.is_bullish and f.bottom > price]
            if not candidates:
                return None
            nearest = min(candidates, key=lambda f: f.bottom)
            if abs(nearest.bottom - price) / price > proximity_pct:
                return None
            return nearest
