"""
ML Signal Service - Provides trade profit probability scoring.

Uses LightGBM classifier trained on historical trade outcomes to score
potential trade entries. Returns a probability of profit for each candidate trade.
"""

import os
import json
import pickle
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path

import numpy as np
import pandas as pd


class MLSignalService:
    """
    ML-based trade signal scoring service.
    
    Loads a trained LightGBM model and provides probability scores
    for potential trades. Falls back gracefully when model unavailable.
    """
    
    MODEL_PATH = Path("models/trade_classifier.pkl")
    FEATURE_CONFIG_PATH = Path("models/feature_config.json")
    OUTCOMES_FILE = Path("logs/trade_outcomes.jsonl")
    
    def __init__(self, logger=None):
        """
        Initialize the ML Signal Service.
        
        Args:
            logger: Optional logger instance for logging predictions
        """
        self._logger = logger
        self._model = None
        self._feature_config = None
        self._is_available = False
        
        # Historical win rate caches
        self._symbol_win_rates: Dict[str, float] = {}
        self._hour_win_rates: Dict[int, float] = {}
        self._win_rates_loaded = False
        
        self._trading_days_collected = 0
        self._min_training_days = 30
        self._ml_data_ready = False
        
        self._load_model()
        self._load_adaptive_settings()
        self._load_historical_win_rates()
        self._count_training_days()
    
    def _load_adaptive_settings(self) -> None:
        """Load adaptive threshold settings from config."""
        try:
            import yaml
            settings_path = Path("config/settings.yaml")
            if settings_path.exists():
                with open(settings_path) as f:
                    settings = yaml.safe_load(f)
                ml_config = settings.get("ml", {})
                adaptive = ml_config.get("adaptive", {})
                
                self._adaptive_enabled = adaptive.get("enabled", False)
                self._low_vix_bonus = adaptive.get("low_vix_bonus", 0.03)
                self._high_vix_penalty = adaptive.get("high_vix_penalty", 0.05)
                self._earnings_bonus = adaptive.get("earnings_bonus", 0.02)
            else:
                self._adaptive_enabled = False
                self._low_vix_bonus = 0.03
                self._high_vix_penalty = 0.05
                self._earnings_bonus = 0.02
        except Exception as e:
            if self._logger:
                self._logger.error(f"Failed to load adaptive settings: {e}")
            self._adaptive_enabled = False
            self._low_vix_bonus = 0.03
            self._high_vix_penalty = 0.05
            self._earnings_bonus = 0.02
    
    def get_adaptive_threshold(self, base_threshold: float, vix: float = 20.0, 
                               is_earnings_season: bool = False) -> float:
        """
        Compute adaptive threshold based on market conditions.
        
        Args:
            base_threshold: The base ML probability threshold
            vix: Current VIX level
            is_earnings_season: Whether we're in earnings season
            
        Returns:
            Adjusted threshold (lower = more permissive, higher = stricter)
        """
        if not self._adaptive_enabled:
            return base_threshold
        
        adjusted = base_threshold
        
        # Low VIX = calmer markets = lower threshold (more opportunities)
        if vix < 15:
            adjusted -= self._low_vix_bonus
        # High VIX = volatile markets = higher threshold (more selective)
        elif vix > 30:
            adjusted += self._high_vix_penalty
        
        # Earnings season = more volatility/opportunity = lower threshold
        if is_earnings_season:
            adjusted -= self._earnings_bonus
        
        # Clamp to reasonable bounds - but respect low thresholds for testing
        # Floor is 5% (prevents negative), ceiling is 75%
        return max(0.05, min(0.75, adjusted))
    
    def _count_training_days(self) -> None:
        """
        Count unique trading days from trade outcomes file.
        
        Tracks how many distinct calendar days have trade data.
        ML gate stays disabled until min_training_days (30) is reached.
        Logs progress on every startup so we can see the countdown.
        """
        try:
            import yaml
            settings_path = Path("config/settings.yaml")
            if settings_path.exists():
                with open(settings_path) as f:
                    settings = yaml.safe_load(f)
                ml_config = settings.get("ml", {})
                self._min_training_days = ml_config.get("min_training_days", 30)
            
            if not self.OUTCOMES_FILE.exists():
                self._trading_days_collected = 0
                self._ml_data_ready = False
                if self._logger:
                    self._logger.log("ml_training_data_status", {
                        "trading_days_collected": 0,
                        "min_required": self._min_training_days,
                        "days_remaining": self._min_training_days,
                        "ml_data_ready": False,
                        "status": "NO DATA - collecting trade outcomes"
                    })
                return
            
            unique_dates = set()
            total_outcomes = 0
            
            with open(self.OUTCOMES_FILE, 'r') as f:
                for line in f:
                    try:
                        outcome = json.loads(line.strip())
                        if outcome.get("is_profitable") is not None:
                            total_outcomes += 1
                            ts = outcome.get("timestamp", outcome.get("exit_time", ""))
                            if ts:
                                date_str = ts[:10] if len(ts) >= 10 else ""
                                if date_str:
                                    unique_dates.add(date_str)
                    except (json.JSONDecodeError, KeyError):
                        continue
            
            self._trading_days_collected = len(unique_dates)
            self._ml_data_ready = self._trading_days_collected >= self._min_training_days
            
            days_remaining = max(0, self._min_training_days - self._trading_days_collected)
            
            if self._logger:
                self._logger.log("ml_training_data_status", {
                    "trading_days_collected": self._trading_days_collected,
                    "min_required": self._min_training_days,
                    "days_remaining": days_remaining,
                    "total_trade_outcomes": total_outcomes,
                    "ml_data_ready": self._ml_data_ready,
                    "status": "READY - sufficient data for ML" if self._ml_data_ready 
                             else f"COLLECTING - {days_remaining} more trading days needed"
                })
                
        except Exception as e:
            if self._logger:
                self._logger.error(f"Failed to count training days: {e}")
            self._trading_days_collected = 0
            self._ml_data_ready = False
    
    @property
    def training_days_collected(self) -> int:
        """Return the number of unique trading days with outcome data."""
        return self._trading_days_collected
    
    @property 
    def ml_data_ready(self) -> bool:
        """Return True if enough trading days have been collected for ML to activate."""
        return self._ml_data_ready
    
    def get_training_status(self) -> Dict[str, Any]:
        """Return ML training data collection status for dashboard/monitoring."""
        days_remaining = max(0, self._min_training_days - self._trading_days_collected)
        return {
            "trading_days_collected": self._trading_days_collected,
            "min_required": self._min_training_days,
            "days_remaining": days_remaining,
            "ml_data_ready": self._ml_data_ready,
            "pct_complete": round(min(100, (self._trading_days_collected / self._min_training_days) * 100), 1)
        }
    
    def _load_historical_win_rates(self) -> None:
        """
        Load historical win rates per symbol and per hour from trade outcomes.
        
        This populates the caches used by the ML model to provide symbol-specific
        and time-specific probability adjustments.
        """
        try:
            if not self.OUTCOMES_FILE.exists():
                if self._logger:
                    self._logger.log("ml_win_rates_no_history", {
                        "status": "no_outcomes_file",
                        "path": str(self.OUTCOMES_FILE)
                    })
                return
            
            # Read outcomes and compute win rates
            symbol_wins: Dict[str, int] = {}
            symbol_total: Dict[str, int] = {}
            hour_wins: Dict[int, int] = {}
            hour_total: Dict[int, int] = {}
            
            with open(self.OUTCOMES_FILE, 'r') as f:
                for line in f:
                    try:
                        outcome = json.loads(line.strip())
                        
                        # Skip incomplete trades
                        if outcome.get("is_profitable") is None:
                            continue
                        
                        symbol = outcome.get("symbol", "UNKNOWN")
                        hour = outcome.get("hour_at_entry", 12)
                        is_win = outcome.get("is_profitable", False)
                        
                        # Symbol stats
                        symbol_total[symbol] = symbol_total.get(symbol, 0) + 1
                        if is_win:
                            symbol_wins[symbol] = symbol_wins.get(symbol, 0) + 1
                        
                        # Hour stats
                        hour_total[hour] = hour_total.get(hour, 0) + 1
                        if is_win:
                            hour_wins[hour] = hour_wins.get(hour, 0) + 1
                            
                    except json.JSONDecodeError:
                        continue
            
            # Compute win rates with Laplace smoothing (avoid 0% or 100%)
            for symbol, total in symbol_total.items():
                wins = symbol_wins.get(symbol, 0)
                # Laplace smoothing: (wins + 1) / (total + 2)
                self._symbol_win_rates[symbol] = (wins + 1) / (total + 2)
            
            for hour, total in hour_total.items():
                wins = hour_wins.get(hour, 0)
                self._hour_win_rates[hour] = (wins + 1) / (total + 2)
            
            self._win_rates_loaded = True
            
            if self._logger:
                self._logger.log("ml_win_rates_loaded", {
                    "symbols": len(self._symbol_win_rates),
                    "hours": len(self._hour_win_rates),
                    "total_outcomes": sum(symbol_total.values())
                })
                
        except Exception as e:
            if self._logger:
                self._logger.error(f"Failed to load historical win rates: {e}")
    
    def get_symbol_win_rate(self, symbol: str) -> float:
        """Get historical win rate for a symbol, default 0.5 if unknown."""
        return self._symbol_win_rates.get(symbol, 0.5)
    
    def get_hour_win_rate(self, hour: int) -> float:
        """Get historical win rate for an hour, default 0.5 if unknown."""
        return self._hour_win_rates.get(hour, 0.5)
    
    def _load_model(self) -> None:
        """Load the trained model and feature configuration."""
        try:
            if self.MODEL_PATH.exists() and self.FEATURE_CONFIG_PATH.exists():
                with open(self.MODEL_PATH, 'rb') as f:
                    self._model = pickle.load(f)
                
                with open(self.FEATURE_CONFIG_PATH, 'r') as f:
                    self._feature_config = json.load(f)
                
                self._is_available = True
                if self._logger:
                    self._logger.log("ml_model_loaded", {
                        "model_path": str(self.MODEL_PATH),
                        "features": len(self._feature_config.get("features", []))
                    })
            else:
                if self._logger:
                    self._logger.log("ml_model_not_found", {
                        "model_path": str(self.MODEL_PATH),
                        "status": "falling_back_to_rules"
                    })
        except Exception as e:
            if self._logger:
                self._logger.error(f"ML model load failed: {e}")
            self._is_available = False
    
    @property
    def is_available(self) -> bool:
        """Check if ML scoring is available."""
        return self._is_available
    
    def score_entry(self, trade_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Score a potential trade entry with profit probability.
        
        Args:
            trade_context: Dictionary containing trade features:
                - symbol: Trading symbol (e.g., "BTC/USD")
                - side: "buy" or "short"
                - price: Current price
                - signal_strength: Rule-based signal strength (0-1)
                - vix: Current VIX level
                - hour: Hour of day (0-23)
                - day_of_week: Day of week (0=Monday, 6=Sunday)
                - rsi: RSI indicator value
                - macd_signal: MACD signal (-1, 0, 1)
                - volatility: Recent volatility measure
                - volume_ratio: Volume vs average ratio
                - account_pnl_pct: Daily P&L percentage
                
        Returns:
            Dictionary with:
                - probability: Profit probability (0.0-1.0)
                - confidence: Model confidence level
                - recommendation: "proceed", "caution", or "skip"
                - threshold_used: The threshold applied
        """
        if not self._is_available:
            return self._fallback_score(trade_context)
        
        try:
            features = self._extract_features(trade_context)
            feature_array = np.array([features])
            
            proba = self._model.predict_proba(feature_array)[0]
            profit_prob = proba[1] if len(proba) > 1 else proba[0]
            
            threshold = self._feature_config.get("threshold", 0.55)
            
            if profit_prob >= threshold + 0.1:
                recommendation = "proceed"
            elif profit_prob >= threshold:
                recommendation = "caution"
            else:
                recommendation = "skip"
            
            result = {
                "probability": float(profit_prob),
                "confidence": float(abs(profit_prob - 0.5) * 2),
                "recommendation": recommendation,
                "threshold_used": threshold,
                "model_version": self._feature_config.get("version", "unknown")
            }
            
            if self._logger:
                self._logger.log("ml_score_entry", {
                    "symbol": trade_context.get("symbol"),
                    "side": trade_context.get("side"),
                    **result
                })
            
            return result
            
        except Exception as e:
            if self._logger:
                self._logger.error(f"ML scoring failed: {e}")
            return self._fallback_score(trade_context)
    
    # Strategy type encoding for options
    STRATEGY_ENCODING = {
        "long_call": 1, "long_put": 2, "covered_call": 3,
        "protective_put": 4, "bull_call_spread": 5, "bear_put_spread": 6,
        "iron_condor": 7, "straddle": 8, "strangle": 9,
        "calendar_spread": 10, "diagonal_spread": 11,
        "buy": 1, "short": -1, "sell": -1, "hold": 0
    }
    
    def _extract_features(self, context: Dict[str, Any]) -> List[float]:
        """Extract feature vector from trade context with string encoding."""
        feature_names = self._feature_config.get("features", [])
        features = []
        
        # Extract symbol and hour for win rate lookup and derived features
        symbol = context.get("symbol", "UNKNOWN")
        hour = context.get("hour", 12)
        day_of_week = context.get("day_of_week", 0)
        
        for fname in feature_names:
            # Inject historical win rates if not provided in context
            if fname == "symbol_win_rate":
                value = context.get(fname, self.get_symbol_win_rate(symbol))
            elif fname == "hour_win_rate":
                value = context.get(fname, self.get_hour_win_rate(hour))
            # Compute derived time features if not provided
            elif fname == "is_morning":
                value = context.get(fname, 1.0 if 6 <= hour < 12 else 0.0)
            elif fname == "is_afternoon":
                value = context.get(fname, 1.0 if 12 <= hour < 18 else 0.0)
            elif fname == "is_weekend":
                value = context.get(fname, 1.0 if day_of_week >= 5 else 0.0)
            else:
                value = context.get(fname, 0.0)
                
            if value is None:
                value = 0.0
            # Handle string values by encoding them
            if isinstance(value, str):
                value = self.STRATEGY_ENCODING.get(value.lower(), 0)
            elif isinstance(value, bool):
                value = 1.0 if value else 0.0
            features.append(float(value))
        
        return features
    
    # Options strategy probability adjustments based on market conditions
    STRATEGY_BASE_PROB = {
        "long_call": 0.52,      # Slightly bullish bias
        "long_put": 0.52,       # Slightly bearish bias  
        "covered_call": 0.58,   # High probability income strategy
        "protective_put": 0.55, # Hedge play
        "bull_call_spread": 0.54,
        "bear_put_spread": 0.54,
        "iron_condor": 0.62,    # High probability range-bound
        "straddle": 0.48,       # Need big move
        "strangle": 0.46,       # Need even bigger move
    }
    
    def _fallback_score(self, trade_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Provide a rule-based fallback score when ML is unavailable.
        
        Uses signal_strength, technical indicators, and cross-asset correlations
        to estimate probability. Enhanced with options-specific logic.
        """
        signal_strength = trade_context.get("signal_strength", 0.5)
        vix = trade_context.get("vix", 20)
        rsi = trade_context.get("rsi", 50)
        side = trade_context.get("side", "buy")
        
        # Check if this is an options strategy (string side like "long_call")
        is_options = isinstance(side, str) and side.lower() in self.STRATEGY_BASE_PROB
        
        if is_options:
            return self._fallback_options_score(trade_context, side.lower(), vix)
        
        # Standard stock/crypto scoring continues below
        
        # Base probability from signal strength
        base_prob = 0.5 + (signal_strength - 0.5) * 0.3
        
        # VIX adjustment (market volatility)
        if vix > 30:
            base_prob *= 0.9
        elif vix < 15:
            base_prob *= 1.05
        
        # RSI adjustment (overbought/oversold)
        if rsi > 70 and side == "buy":
            base_prob *= 0.85
        elif rsi < 30 and side == "short":
            base_prob *= 0.85
        elif rsi < 30 and side == "buy":
            base_prob *= 1.05  # Oversold buying opportunity
        elif rsi > 70 and side == "short":
            base_prob *= 1.05  # Overbought shorting opportunity
        
        # Volume confirmation (volume_zscore > 1 means above-average volume)
        volume_zscore = trade_context.get("volume_zscore", 0.0)
        if volume_zscore > 1.0:
            base_prob *= 1.05  # Higher volume confirms signal
        elif volume_zscore < -1.0:
            base_prob *= 0.95  # Low volume weakens signal
        
        # Bollinger Band position (0=lower band, 1=upper band)
        bb_position = trade_context.get("bb_position", 0.5)
        if side == "buy" and bb_position < 0.2:
            base_prob *= 1.05  # Near lower band, good buy entry
        elif side == "short" and bb_position > 0.8:
            base_prob *= 1.05  # Near upper band, good short entry
        
        # MACD momentum confirmation
        macd_histogram = trade_context.get("macd_histogram", 0.0)
        if side == "buy" and macd_histogram > 0:
            base_prob *= 1.03  # Bullish MACD
        elif side == "short" and macd_histogram < 0:
            base_prob *= 1.03  # Bearish MACD
        
        # Cross-asset correlation (diversification benefit)
        btc_correlation = trade_context.get("btc_correlation", 0.0)
        if abs(btc_correlation) < 0.3:
            base_prob *= 1.02  # Low correlation = better diversification
        elif abs(btc_correlation) > 0.8:
            base_prob *= 0.98  # High correlation = more risk
        
        # Sector momentum alignment
        sector_momentum = trade_context.get("sector_momentum", 0.0)
        if side == "buy" and sector_momentum > 0.02:
            base_prob *= 1.03  # Sector trending up
        elif side == "short" and sector_momentum < -0.02:
            base_prob *= 1.03  # Sector trending down
        
        # Relative strength
        relative_strength = trade_context.get("relative_strength", 0.0)
        if side == "buy" and relative_strength > 0.05:
            base_prob *= 1.02  # Asset outperforming
        elif side == "short" and relative_strength < -0.05:
            base_prob *= 1.02  # Asset underperforming
        
        # Drawdown probability adjustment with institutional ceiling
        drawdown_prob = trade_context.get("drawdown_probability", 0.0)
        if drawdown_prob > 0.3:
            base_prob *= 0.95  # Reduce conviction in high drawdown probability
            # Institutional ceiling: Cap probability when drawdown risk is elevated
            max_prob_at_high_dd = 0.65 - (drawdown_prob - 0.3) * 0.5
            base_prob = min(base_prob, max_prob_at_high_dd)
        
        base_prob = max(0.0, min(1.0, base_prob))
        
        return {
            "probability": base_prob,
            "confidence": 0.3,
            "recommendation": "proceed" if base_prob > 0.55 else "caution",
            "threshold_used": 0.55,
            "model_version": "fallback_v1"
        }
    
    def _fallback_options_score(self, context: Dict[str, Any], strategy: str, vix: float) -> Dict[str, Any]:
        """
        Options-specific fallback scoring based on strategy type and market conditions.
        
        Each options strategy has different probability profiles based on VIX and IV.
        """
        base_prob = self.STRATEGY_BASE_PROB.get(strategy, 0.50)
        iv_percentile = context.get("iv_percentile", 50)
        
        # VIX-based adjustments per strategy
        if strategy in ["long_call", "long_put"]:
            # Directional options benefit from low VIX (cheaper premiums)
            if vix < 15:
                base_prob += 0.08  # Low VIX = cheap options
            elif vix > 25:
                base_prob -= 0.05  # High VIX = expensive premiums
        
        elif strategy in ["iron_condor", "covered_call"]:
            # Premium-selling strategies benefit from high VIX
            if vix > 25:
                base_prob += 0.06  # High VIX = more premium collected
            elif vix < 15:
                base_prob -= 0.04  # Low VIX = less premium
        
        elif strategy in ["straddle", "strangle"]:
            # Volatility plays need VIX expansion
            if vix < 18 and iv_percentile < 30:
                base_prob += 0.10  # Low IV = cheap straddles before expansion
            elif vix > 30:
                base_prob -= 0.08  # Already elevated, less room for expansion
        
        # IV percentile adjustments
        if iv_percentile < 25:
            # Low IV environment - favor buying premium
            if strategy in ["long_call", "long_put", "straddle", "strangle"]:
                base_prob += 0.05
        elif iv_percentile > 75:
            # High IV environment - favor selling premium
            if strategy in ["iron_condor", "covered_call", "bull_call_spread", "bear_put_spread"]:
                base_prob += 0.05
            elif strategy in ["long_call", "long_put"]:
                base_prob -= 0.05
        
        # Time of day adjustment (morning edge)
        hour = context.get("hour", 12)
        if 6 <= hour <= 8:  # Morning PST
            base_prob += 0.02  # Opening volatility edge
        
        base_prob = max(0.0, min(1.0, base_prob))
        
        return {
            "probability": base_prob,
            "confidence": 0.4,
            "recommendation": "proceed" if base_prob > 0.55 else "caution",
            "threshold_used": 0.55,
            "model_version": "fallback_options_v1"
        }
    
    def batch_score(self, trade_contexts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Score multiple trade entries at once.
        
        More efficient than calling score_entry repeatedly.
        """
        return [self.score_entry(ctx) for ctx in trade_contexts]
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about the loaded model."""
        if not self._is_available:
            return {"status": "unavailable", "reason": "model_not_loaded"}
        
        return {
            "status": "available",
            "version": self._feature_config.get("version", "unknown"),
            "features": self._feature_config.get("features", []),
            "threshold": self._feature_config.get("threshold", 0.55),
            "trained_on": self._feature_config.get("trained_on", "unknown"),
            "accuracy": self._feature_config.get("accuracy", None)
        }
