"""
Session Protection - Daily Profit Protection & Freeroll System.

Three interconnected profit preservation systems:

  1. Daily Target Lock: When realized P&L crosses configurable tiers,
     lock a percentage of the ACTUAL P&L at that moment as a floor.
     ALL new entries blocked after any lock activates.

  2. Freeroll System: After a target lock, allow exactly ONE additional
     entry if the setup has an elite quality score (>= threshold).
     Position sized ONLY to "house money" (P&L above locked floor).
     If freeroll loses everything, you keep the locked floor.

  3. Spam Throttle: Blocked-entry messages throttled to once per
     N minutes per unique reason to reduce log noise.

Design:
  - State persisted in SQLite via get_state/set_state (survives restarts)
  - Lock uses ACTUAL realized P&L at crossing, not the tier dollar amount
  - Fail-open: errors never block trading
  - Session-scoped: resets daily

Config tiers (default):
  $300  "DAILY GOAL"  → lock 85% as floor
  $500  "STRONG DAY"  → lock 90% as floor
  $1000 "HOME RUN"    → lock 92% as floor
  $3000 "JACKPOT"     → lock 95% as floor
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timezone


@dataclass
class LockTier:
    """A single target lock tier."""
    threshold_usd: float
    retain_pct: float
    label: str


@dataclass
class SessionProtectionConfig:
    """Configuration for SessionProtection from settings.yaml."""
    enabled: bool = False

    # Target Lock Tiers (threshold_usd, retain_pct, label)
    lock_tiers: List[LockTier] = field(default_factory=lambda: [
        LockTier(300, 85.0, "DAILY GOAL"),
        LockTier(500, 90.0, "STRONG DAY"),
        LockTier(1000, 92.0, "HOME RUN"),
        LockTier(3000, 95.0, "JACKPOT"),
    ])

    # HWM Giveback Cap (pre-lock protection)
    hwm_giveback_enabled: bool = True
    hwm_max_giveback_pct: float = 25.0
    hwm_min_peak_usd: float = 400.0

    # Trailing Tighten (ProfitSniper ratchet tightening)
    tighten_enabled: bool = True
    tighten_target_usd: float = 1500.0
    tighten_factor: float = 0.50

    # Freeroll System
    freeroll_enabled: bool = True
    freeroll_min_quality_score: float = 90.0
    freeroll_min_house_money_usd: float = 50.0

    # Spam Throttle
    spam_throttle_minutes: float = 5.0

    @classmethod
    def from_yaml(cls, d: dict) -> "SessionProtectionConfig":
        """Build config from settings.yaml session_protection block."""
        if not d:
            return cls()

        tiers_raw = d.get("lock_tiers", None)
        if tiers_raw and isinstance(tiers_raw, list):
            tiers = []
            for t in tiers_raw:
                if isinstance(t, dict):
                    tiers.append(LockTier(
                        threshold_usd=float(t.get("threshold_usd", 0)),
                        retain_pct=float(t.get("retain_pct", 90)),
                        label=str(t.get("label", "LOCK")),
                    ))
            if not tiers:
                tiers = cls().lock_tiers
        else:
            tiers = cls().lock_tiers

        return cls(
            enabled=d.get("enabled", False),
            lock_tiers=sorted(tiers, key=lambda t: t.threshold_usd),
            hwm_giveback_enabled=d.get("hwm_giveback_enabled", True),
            hwm_max_giveback_pct=float(d.get("hwm_max_giveback_pct", 25.0)),
            hwm_min_peak_usd=float(d.get("hwm_min_peak_usd", 400.0)),
            tighten_enabled=d.get("tighten_enabled", True),
            tighten_target_usd=float(d.get("tighten_target_usd", 1500.0)),
            tighten_factor=float(d.get("tighten_factor", 0.50)),
            freeroll_enabled=d.get("freeroll_enabled", True),
            freeroll_min_quality_score=float(d.get("freeroll_min_quality_score", 90.0)),
            freeroll_min_house_money_usd=float(d.get("freeroll_min_house_money_usd", 50.0)),
            spam_throttle_minutes=float(d.get("spam_throttle_minutes", 5.0)),
        )


# State keys for persistence (survive restarts)
_STATE_PREFIX = "session_prot_"
_KEY_SESSION_DATE = f"{_STATE_PREFIX}date"
_KEY_REALIZED_PNL = f"{_STATE_PREFIX}realized_pnl"
_KEY_HWM = f"{_STATE_PREFIX}hwm"
_KEY_TRADE_COUNT = f"{_STATE_PREFIX}trade_count"
_KEY_TARGET_LOCKED = f"{_STATE_PREFIX}target_locked"
_KEY_LOCK_LEVEL = f"{_STATE_PREFIX}lock_level"
_KEY_LOCK_PNL = f"{_STATE_PREFIX}lock_pnl"
_KEY_LOCKED_FLOOR = f"{_STATE_PREFIX}locked_floor"
_KEY_FREEROLL_USED = f"{_STATE_PREFIX}freeroll_used"
_KEY_FREEROLL_TRADE_ID = f"{_STATE_PREFIX}freeroll_trade_id"


class SessionProtection:
    """
    Daily session-level profit protection with target locks and freeroll.

    Three systems:
    1. Target Lock: Blocks new entries once P&L crosses a tier.
       Floor = actual_pnl_at_crossing * retain_pct.
    2. Freeroll: One elite entry allowed after lock, sized to house money.
    3. Spam Throttle: Reduces repeated blocked-entry log noise.

    State persisted in SQLite for crash/restart survival.
    Resets daily on first call after midnight ET.
    """

    def __init__(self, config: SessionProtectionConfig):
        self._config = config
        self._session_date: Optional[str] = None

        # Session state (also persisted to SQLite)
        self._realized_pnl: float = 0.0
        self._hwm: float = 0.0
        self._trade_count: int = 0
        self._trades: List[Dict] = []

        # Target lock state
        self._target_locked: bool = False
        self._target_lock_level: str = ""
        self._target_lock_pnl: float = 0.0
        self._locked_floor: float = 0.0

        # Freeroll state
        self._freeroll_used: bool = False
        self._freeroll_trade_id: str = ""

        # Spam throttle: {reason_key: last_log_time}
        self._spam_last_log: Dict[str, float] = {}

        self._init_session()

    def _init_session(self):
        """Initialize or reset session state. Restores from SQLite if same day."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._session_date == today:
            return

        try:
            from ..core.state import get_state, set_state
            saved_date = get_state(_KEY_SESSION_DATE)
            if saved_date == today:
                self._session_date = today
                self._realized_pnl = float(get_state(_KEY_REALIZED_PNL, 0.0))
                self._hwm = float(get_state(_KEY_HWM, 0.0))
                self._trade_count = int(get_state(_KEY_TRADE_COUNT, 0))
                self._target_locked = bool(get_state(_KEY_TARGET_LOCKED, False))
                self._target_lock_level = str(get_state(_KEY_LOCK_LEVEL, ""))
                self._target_lock_pnl = float(get_state(_KEY_LOCK_PNL, 0.0))
                self._locked_floor = float(get_state(_KEY_LOCKED_FLOOR, 0.0))
                self._freeroll_used = bool(get_state(_KEY_FREEROLL_USED, False))
                self._freeroll_trade_id = str(get_state(_KEY_FREEROLL_TRADE_ID, ""))
                return
            else:
                self._session_date = today
                self._realized_pnl = 0.0
                self._hwm = 0.0
                self._trade_count = 0
                self._trades = []
                self._target_locked = False
                self._target_lock_level = ""
                self._target_lock_pnl = 0.0
                self._locked_floor = 0.0
                self._freeroll_used = False
                self._freeroll_trade_id = ""
                self._spam_last_log = {}
                set_state(_KEY_SESSION_DATE, today)
                self._persist_state()
        except Exception:
            self._session_date = today
            self._realized_pnl = 0.0
            self._hwm = 0.0
            self._trade_count = 0
            self._trades = []
            self._target_locked = False
            self._target_lock_level = ""
            self._target_lock_pnl = 0.0
            self._locked_floor = 0.0
            self._freeroll_used = False
            self._freeroll_trade_id = ""
            self._spam_last_log = {}

    def _persist_state(self):
        """Persist critical state fields to SQLite."""
        try:
            from ..core.state import set_state
            set_state(_KEY_SESSION_DATE, self._session_date)
            set_state(_KEY_REALIZED_PNL, self._realized_pnl)
            set_state(_KEY_HWM, self._hwm)
            set_state(_KEY_TRADE_COUNT, self._trade_count)
            set_state(_KEY_TARGET_LOCKED, self._target_locked)
            set_state(_KEY_LOCK_LEVEL, self._target_lock_level)
            set_state(_KEY_LOCK_PNL, self._target_lock_pnl)
            set_state(_KEY_LOCKED_FLOOR, self._locked_floor)
            set_state(_KEY_FREEROLL_USED, self._freeroll_used)
            set_state(_KEY_FREEROLL_TRADE_ID, self._freeroll_trade_id)
        except Exception:
            pass

    def update_config(self, config: SessionProtectionConfig):
        """Hot-reload config without resetting session state."""
        self._config = config

    def record_trade_pnl(self, pnl: float, symbol: str, reason: str = ""):
        """
        Record a realized trade P&L.

        Updates cumulative session P&L, HWM, and checks target locks.
        Called by ExitBot after every position exit.
        """
        self._init_session()

        if not self._config.enabled:
            return

        self._realized_pnl += pnl
        self._trade_count += 1
        self._trades.append({
            "symbol": symbol,
            "pnl": round(pnl, 2),
            "reason": reason,
            "cumulative": round(self._realized_pnl, 2),
            "timestamp": time.time(),
        })

        if self._realized_pnl > self._hwm:
            self._hwm = self._realized_pnl

        self._check_target_locks()
        self._persist_state()

    def _check_target_locks(self):
        """Check if P&L crossed any tier and apply target lock.

        Key behavior: locks at the ACTUAL P&L at crossing, not the tier amount.
        E.g., if you cross the $3000 tier at $7000, floor = 95% of $7000 = $6650.
        Higher tiers upgrade the lock (higher retain_pct applied to current P&L).
        """
        if not self._config.lock_tiers:
            return

        best_tier: Optional[LockTier] = None
        for tier in self._config.lock_tiers:
            if self._realized_pnl >= tier.threshold_usd:
                best_tier = tier

        if best_tier is None:
            return

        new_floor = self._realized_pnl * (best_tier.retain_pct / 100.0)

        if new_floor > self._locked_floor or not self._target_locked:
            old_floor = self._locked_floor
            self._locked_floor = new_floor
            self._target_locked = True
            self._target_lock_level = best_tier.label
            self._target_lock_pnl = self._realized_pnl

            print(f"\n{'='*60}")
            print(f"  TARGET LOCK: {best_tier.label}")
            print(f"  P&L at lock: ${self._realized_pnl:,.0f}")
            print(f"  Locked floor: ${new_floor:,.0f} ({best_tier.retain_pct}% retained)")
            print(f"  House money: ${max(0, self._realized_pnl - new_floor):,.0f}")
            if old_floor > 0:
                print(f"  (upgraded from ${old_floor:,.0f} floor)")
            if self._config.freeroll_enabled and not self._freeroll_used:
                house = self._realized_pnl - new_floor
                print(f"  Freeroll: AVAILABLE (${house:,.0f} house money)")
            elif self._freeroll_used:
                print(f"  Freeroll: USED")
            print(f"  All new entries BLOCKED (except freeroll)")
            print(f"{'='*60}\n")

    def should_block_new_trade(self, quality_score: float = 0.0) -> Tuple[bool, str]:
        """
        Check if new trades should be blocked.

        Returns (should_block, reason).

        If target is locked but freeroll conditions are met, returns
        (False, "FREEROLL:$XXX") where $XXX is the max house money budget.
        The caller must size the position to that budget.

        Args:
            quality_score: 0-100 quality score of the proposed entry.
                          Must be >= freeroll_min_quality_score for freeroll.
        """
        self._init_session()

        if not self._config.enabled:
            return False, ""

        # TARGET LOCK: entries blocked after any tier is hit
        if self._target_locked:
            house_money = max(0, self._realized_pnl - self._locked_floor)

            # Check freeroll eligibility
            if (self._config.freeroll_enabled
                    and not self._freeroll_used
                    and quality_score >= self._config.freeroll_min_quality_score
                    and house_money >= self._config.freeroll_min_house_money_usd):
                return False, f"FREEROLL:${house_money:.0f}"

            freeroll_status = ""
            if self._config.freeroll_enabled and not self._freeroll_used:
                freeroll_status = f" FR:${house_money:.0f} avail (need score>={self._config.freeroll_min_quality_score:.0f})"
            elif self._freeroll_used:
                freeroll_status = " FR:USED"

            return True, (
                f"TARGET LOCKED [{self._target_lock_level}]: "
                f"P&L ${self._realized_pnl:,.0f}, floor ${self._locked_floor:,.0f}, "
                f"house ${house_money:,.0f}.{freeroll_status}"
            )

        # HWM Giveback Cap: block if giveback from peak exceeds threshold
        if self._config.hwm_giveback_enabled:
            if self._hwm >= self._config.hwm_min_peak_usd:
                giveback = self._hwm - self._realized_pnl
                max_giveback = self._hwm * (self._config.hwm_max_giveback_pct / 100.0)
                if giveback > max_giveback:
                    return True, (
                        f"HWM giveback cap: gave back ${giveback:.0f} from "
                        f"${self._hwm:.0f} peak (>{self._config.hwm_max_giveback_pct}%)"
                    )

        return False, ""

    def mark_freeroll_used(self, trade_id: str):
        """Mark the freeroll as used after a freeroll entry is placed."""
        self._freeroll_used = True
        self._freeroll_trade_id = trade_id
        self._persist_state()
        print(f"  [SESSION] Freeroll USED — trade {trade_id}")

    def get_house_money(self) -> float:
        """Return current house money (P&L above locked floor). 0 if no lock."""
        if not self._target_locked:
            return 0.0
        return max(0.0, self._realized_pnl - self._locked_floor)

    def is_target_locked(self) -> bool:
        """Return whether a target lock is active."""
        return self._target_locked

    def is_freeroll_available(self) -> bool:
        """Return whether the freeroll entry is available."""
        return (self._target_locked
                and self._config.freeroll_enabled
                and not self._freeroll_used
                and self.get_house_money() >= self._config.freeroll_min_house_money_usd)

    def should_force_exit_to_protect_floor(self) -> Tuple[bool, float]:
        """
        Check if positions should be force-exited to protect the locked floor.

        Returns (should_force, floor_usd).
        ExitBot should call this and close positions if P&L drops below floor.
        """
        self._init_session()
        if not self._config.enabled or not self._target_locked:
            return False, 0.0

        if self._realized_pnl < self._locked_floor:
            return True, self._locked_floor

        return False, 0.0

    def should_throttle_message(self, reason_key: str) -> bool:
        """
        Check if a blocked-entry message should be throttled (suppressed).

        Returns True if the message was logged recently and should be skipped.
        """
        now = time.time()
        throttle_seconds = self._config.spam_throttle_minutes * 60
        last = self._spam_last_log.get(reason_key, 0.0)
        if now - last < throttle_seconds:
            return True
        self._spam_last_log[reason_key] = now
        return False

    def get_tighten_factor(self) -> Tuple[bool, float]:
        """Check if ProfitSniper ratchets should be tightened."""
        self._init_session()

        if not self._config.enabled or not self._config.tighten_enabled:
            return False, 0.0

        if self._realized_pnl >= self._config.tighten_target_usd:
            return True, self._config.tighten_factor

        return False, 0.0

    def get_session_status(self) -> Dict:
        """Return current session protection state for logging/display."""
        self._init_session()

        giveback = max(0, self._hwm - self._realized_pnl)
        giveback_pct = (giveback / self._hwm * 100) if self._hwm > 0 else 0
        house_money = max(0, self._realized_pnl - self._locked_floor) if self._target_locked else 0

        should_tighten, tighten_factor = self.get_tighten_factor()
        should_block, block_reason = self.should_block_new_trade()

        return {
            "enabled": self._config.enabled,
            "session_date": self._session_date,
            "realized_pnl_usd": round(self._realized_pnl, 2),
            "hwm_usd": round(self._hwm, 2),
            "giveback_usd": round(giveback, 2),
            "giveback_pct": round(giveback_pct, 1),
            "target_locked": self._target_locked,
            "lock_level": self._target_lock_level,
            "lock_pnl": round(self._target_lock_pnl, 2),
            "locked_floor_usd": round(self._locked_floor, 2),
            "house_money_usd": round(house_money, 2),
            "freeroll_available": self.is_freeroll_available(),
            "freeroll_used": self._freeroll_used,
            "freeroll_trade_id": self._freeroll_trade_id,
            "trade_count": self._trade_count,
            "tighten_active": should_tighten,
            "tighten_factor": tighten_factor,
            "entries_blocked": should_block,
            "block_reason": block_reason,
        }

    def get_console_line(self) -> str:
        """Return a single-line summary for console display."""
        if not self._config.enabled:
            return "Session Protection: OFF"

        status = self.get_session_status()
        pnl = status["realized_pnl_usd"]
        floor = status["locked_floor_usd"]
        house = status["house_money_usd"]

        if status["target_locked"]:
            mode = f"LOCKED [{status['lock_level']}]"
        elif status["entries_blocked"]:
            mode = "BLOCKED"
        else:
            mode = "ACTIVE"

        parts = [f"Session: {mode}"]
        parts.append(f"P&L=${pnl:+,.0f}")
        if status["target_locked"]:
            parts.append(f"Floor=${floor:,.0f}")
            if status["freeroll_available"]:
                parts.append(f"FR:${house:,.0f}")
            elif status["freeroll_used"]:
                parts.append("FR:USED")
        elif status["hwm_usd"] > 0:
            parts.append(f"HWM=${status['hwm_usd']:,.0f}")
        parts.append(f"Trades={status['trade_count']}")

        return " | ".join(parts)


# Singleton pattern
_session_protection_instance: Optional[SessionProtection] = None


def get_session_protection(config: Optional[SessionProtectionConfig] = None) -> SessionProtection:
    """Get or create the singleton SessionProtection instance.

    If config is provided and the singleton already exists, update_config is called
    to ensure the latest config is always applied (eliminates race conditions where
    a bot creates the singleton with defaults before ExitBot initializes with YAML config).
    """
    global _session_protection_instance
    if _session_protection_instance is None:
        if config is None:
            config = _load_config_from_yaml()
        _session_protection_instance = SessionProtection(config)
    elif config is not None:
        _session_protection_instance.update_config(config)
    return _session_protection_instance


def _load_config_from_yaml() -> SessionProtectionConfig:
    """Load SessionProtection config from settings.yaml as fallback."""
    try:
        import os, yaml
        base = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        settings_path = os.path.join(base, "config", "settings.yaml")
        if not os.path.exists(settings_path):
            base = os.path.dirname(base)
            settings_path = os.path.join(base, "config", "settings.yaml")
        with open(settings_path, "r") as f:
            settings = yaml.safe_load(f) or {}
        sp_dict = settings.get("session_protection", {})
        return SessionProtectionConfig.from_yaml(sp_dict)
    except Exception:
        return SessionProtectionConfig()
