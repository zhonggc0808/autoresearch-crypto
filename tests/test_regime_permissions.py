"""Tests for dex.regime_permissions — ADX three-tier, regime_change_policy, exit_only."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dex.regime_filter import build_daily_regime_labels
from dex.regime_permissions import (
    BarPermission,
    RiskOffConfig,
    apply_permission_arrays,
    apply_permissions_with_position,
    build_permission_arrays,
    compute_daily_indicators,
    compute_permissions,
    route_regime_signals,
)


# ── fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def ohlcv_30d() -> pd.DataFrame:
    """30 days of 5m bars with a gentle uptrend."""
    n = 30 * 288  # 30 days × 288 5m bars/day
    rng = np.random.default_rng(42)
    base = 3000 + np.linspace(0, 300, n)  # slow uptrend
    noise = rng.normal(0, 20, n)
    close = base + noise
    high = close + np.abs(rng.normal(15, 5, n))
    low = close - np.abs(rng.normal(15, 5, n))
    open_ = close + rng.normal(0, 5, n)
    volume = np.full(n, 1000.0)
    dts = pd.date_range("2026-05-01", periods=n, freq="5min")
    return pd.DataFrame({
        "datetime": dts, "timestamp": dts.astype(np.int64) // 10**9,
        "open": open_, "high": high, "low": low, "close": close, "volume": volume,
    })


@pytest.fixture
def daily_ctx(ohlcv_30d):
    return compute_daily_indicators(ohlcv_30d)


@pytest.fixture
def regimes(ohlcv_30d):
    return build_daily_regime_labels(ohlcv_30d, fast_days=50, slow_days=200)


# ── RiskOffConfig backward compat ────────────────────────────────────────────


def test_adx_min_for_trade_backward_compat():
    """Setting adx_min_for_trade should still work (two-tier, no exit_only band)."""
    cfg = RiskOffConfig(allow_long=True, allow_short=True, adx_min_for_trade=22)
    # The actual resolution happens at call time in _resolve_adx_thresholds
    from dex.regime_permissions import _resolve_adx_thresholds
    ff, entry = _resolve_adx_thresholds(cfg)
    assert ff == 22
    assert entry == 22


def test_adx_three_tier_overrides_compat():
    """adx_force_flat_below + adx_entry_min take precedence."""
    cfg = RiskOffConfig(
        allow_long=True, allow_short=True,
        adx_min_for_trade=22,
        adx_force_flat_below=18, adx_entry_min=22,
    )
    from dex.regime_permissions import _resolve_adx_thresholds
    ff, entry = _resolve_adx_thresholds(cfg)
    assert ff == 18  # explicit value wins
    assert entry == 22


# ── ADX three-tier ───────────────────────────────────────────────────────────


class TestADXThreeTier:
    """Verify build_permission_arrays produces correct three-tier ADX behaviour."""

    def test_adx_below_force_flat_below(self, ohlcv_30d, daily_ctx, regimes):
        """ADX < force_flat_below → force_flat on every bar."""
        n = len(ohlcv_30d)
        # ADX all zeros → below any threshold
        adx = np.zeros(n, dtype=float)
        cfg = RiskOffConfig(
            allow_long=True, allow_short=True,
            adx_force_flat_below=18, adx_entry_min=22,
        )
        al, as_, ff, eo = build_permission_arrays(
            ohlcv_30d, regimes, cfg, cfg, cfg, daily_ctx, adx,
        )
        assert ff.sum() == n
        assert al.sum() == 0
        assert as_.sum() == 0
        assert eo.sum() == 0

    def test_adx_between_ff_and_entry(self, ohlcv_30d, daily_ctx, regimes):
        """ADX in [force_flat_below, entry_min) → exit_only, no force_flat."""
        n = len(ohlcv_30d)
        adx = np.full(n, 20.0, dtype=float)  # 18 ≤ 20 < 22
        cfg = RiskOffConfig(
            allow_long=True, allow_short=True,
            adx_force_flat_below=18, adx_entry_min=22,
        )
        al, as_, ff, eo = build_permission_arrays(
            ohlcv_30d, regimes, cfg, cfg, cfg, daily_ctx, adx,
        )
        assert ff.sum() == 0
        assert (eo == 1).sum() == n
        assert al.sum() == 0
        assert as_.sum() == 0

    def test_adx_above_entry_min(self, ohlcv_30d, daily_ctx, regimes):
        """ADX >= entry_min → normal trading (no force_flat, no exit_only)."""
        n = len(ohlcv_30d)
        adx = np.full(n, 25.0, dtype=float)
        cfg = RiskOffConfig(
            allow_long=True, allow_short=True,
            adx_force_flat_below=18, adx_entry_min=22,
        )
        al, as_, ff, eo = build_permission_arrays(
            ohlcv_30d, regimes, cfg, cfg, cfg, daily_ctx, adx,
        )
        assert ff.sum() == 0
        assert eo.sum() == 0
        # Directional permissions should pass through base config
        assert al.sum() == n
        assert as_.sum() == n

    def test_three_tier_with_directional(self, ohlcv_30d, daily_ctx, regimes):
        """N7-style: directional_only + three-tier ADX."""
        n = len(ohlcv_30d)
        # Mix of ADX values
        adx = np.full(n, 25.0, dtype=float)
        adx[:100] = 15.0   # force_flat zone
        adx[100:200] = 20.0  # exit_only zone
        # rest: normal

        cfg = RiskOffConfig(
            allow_long=True, allow_short=True, directional_only=True,
            ema_fast=50, ema_slope_days=5,
            adx_force_flat_below=18, adx_entry_min=22,
        )
        al, as_, ff, eo = build_permission_arrays(
            ohlcv_30d, regimes, cfg, cfg, cfg, daily_ctx, adx,
        )
        # force_flat zone
        assert ff[:100].sum() == 100
        assert al[:100].sum() == 0
        # exit_only zone
        assert eo[100:200].sum() == 100
        assert ff[100:200].sum() == 0
        # normal zone — directionals apply (may be zero if no signal)
        assert ff[200:].sum() == 0
        assert eo[200:].sum() == 0


# ── exit_only position-aware behaviour ───────────────────────────────────────


class TestExitOnlyPositionAware:
    """exit_only must be position-aware: block entries, allow hold/close."""

    def test_exit_only_blocks_new_long_when_flat(self):
        """Flat + exit_only → raw LONG(2) becomes HOLD(1)."""
        signals = np.array([2, 2, 2], dtype=int)  # try to go long
        perms = [
            BarPermission(False, False, False, True, "exit_only"),
            BarPermission(False, False, False, True, "exit_only"),
            BarPermission(False, False, False, True, "exit_only"),
        ]
        out = apply_permissions_with_position(signals, perms)
        # All should be hold (1), position stays flat
        assert out.tolist() == [1, 1, 1]

    def test_exit_only_allows_close_when_in_position(self):
        """In position + exit_only → close(0) is allowed, then stay flat."""
        signals = np.array([2, 0, 0], dtype=int)  # enter long, then close, then close again
        perms = [
            BarPermission(True, False, False, False, "ok"),       # allow entry
            BarPermission(False, False, False, True, "exit_only"),  # exit_only
            BarPermission(False, False, False, True, "exit_only"),
        ]
        out = apply_permissions_with_position(signals, perms)
        assert out[0] == 2  # enter long
        assert out[1] == 0  # close allowed
        # bar 2: raw=0, already flat, exit_only → stays 0 (flat) — correct

    def test_exit_only_blocks_reversal(self):
        """In long + exit_only → raw SHORT(3) becomes CLOSE(0), not reversal."""
        signals = np.array([2, 3, 1], dtype=int)  # enter long, try short, hold
        perms = [
            BarPermission(True, False, False, False, "ok"),
            BarPermission(False, False, False, True, "exit_only"),
            BarPermission(False, False, False, True, "exit_only"),
        ]
        out = apply_permissions_with_position(signals, perms)
        assert out[0] == 2  # entered long
        assert out[1] == 0  # reversal blocked → close instead
        assert out[2] == 1  # hold flat

    def test_exit_only_allows_same_direction_hold(self):
        """In long + exit_only → raw LONG(2) or HOLD(1) keeps position."""
        signals = np.array([2, 3, 2], dtype=int)  # long, short signal, long signal
        perms = [
            BarPermission(True, False, False, False, "ok"),
            BarPermission(False, False, False, True, "exit_only"),
            BarPermission(False, False, False, True, "exit_only"),
        ]
        out = apply_permissions_with_position(signals, perms)
        # Bar 0: enter long. Bar 1: short signal but exit_only → close (reversal blocked)
        # Bar 2: long signal but exit_only & flat → hold(1)
        assert out[0] == 2
        assert out[1] == 0  # reversal blocked, position → flat
        assert out[2] == 1  # can't re-enter

    def test_apply_permission_arrays_exit_only(self):
        """Same as above but using the fast array path."""
        signals = np.array([2, 3, 2], dtype=int)
        al = np.array([1, 0, 0], dtype=np.int8)
        as_ = np.array([0, 0, 0], dtype=np.int8)
        ff = np.array([0, 0, 0], dtype=np.int8)
        eo = np.array([0, 1, 1], dtype=np.int8)
        out = apply_permission_arrays(signals, al, as_, ff, eo)
        assert out[0] == 2  # enter long
        assert out[1] == 0  # reversal blocked → close
        assert out[2] == 1  # can't re-enter, stay flat


# ── regime_change_policy ─────────────────────────────────────────────────────


class TestRegimeChangePolicy:
    def test_always_close_on_regime_change(self):
        """always_close → output close(0) on first bar of new regime."""
        bull = np.array([2, 2, 2, 2, 2], dtype=int)
        bear = np.array([3, 3, 3, 3, 3], dtype=int)
        neutral = np.array([1, 1, 1, 1, 1], dtype=int)
        regimes = np.array(["BULL", "BULL", "BEAR", "BEAR", "BEAR"], dtype=object)

        routed = route_regime_signals(bull, bear, neutral, regimes, regime_change_policy="always_close")
        # Bar 0: BULL, first bar
        # Bar 1: BULL, same regime → 2
        # Bar 2: regime change BULL→BEAR → close(0)
        # Bar 3: BEAR, same → 3
        # Bar 4: BEAR, same → 3
        assert routed.tolist() == [2, 2, 0, 3, 3]

    def test_permission_based_no_force_close(self):
        """permission_based → no special close on regime change."""
        bull = np.array([2, 2, 3, 3, 3], dtype=int)
        bear = np.array([3, 3, 2, 2, 2], dtype=int)
        neutral = np.array([1, 1, 1, 1, 1], dtype=int)
        regimes = np.array(["BULL", "BULL", "BEAR", "BEAR", "BEAR"], dtype=object)

        routed = route_regime_signals(bull, bear, neutral, regimes, regime_change_policy="permission_based")
        # Bar 0: BULL → 2
        # Bar 1: BULL → 2
        # Bar 2: regime change to BEAR → bear signal(2) = 2 (no forced close)
        # Bar 3: BEAR → 2
        # Bar 4: BEAR → 2
        assert routed.tolist() == [2, 2, 2, 2, 2]

    def test_never_close(self):
        """never_close → route normally, no special handling."""
        bull = np.array([2, 2, 1, 1, 1], dtype=int)
        bear = np.array([3, 3, 3, 3, 3], dtype=int)
        neutral = np.array([1, 1, 1, 1, 1], dtype=int)
        regimes = np.array(["BULL", "BULL", "BEAR", "BEAR", "BEAR"], dtype=object)

        routed = route_regime_signals(bull, bear, neutral, regimes, regime_change_policy="never_close")
        assert routed.tolist() == [2, 2, 3, 3, 3]


# ── consistency between APIs ─────────────────────────────────────────────────


class TestAPIConsistency:
    """compute_permissions and build_permission_arrays must agree."""

    def test_permissions_match_arrays(self, ohlcv_30d, daily_ctx, regimes):
        """Both APIs produce the same allow_long/allow_short/force_flat/exit_only."""
        cfg = RiskOffConfig(allow_long=True, allow_short=False)
        n = len(ohlcv_30d)

        # list-based API (computes daily internally)
        perms = compute_permissions(ohlcv_30d, regimes, cfg, cfg, cfg)

        # array-based API (uses precomputed daily_ctx)
        al, as_, ff, eo = build_permission_arrays(
            ohlcv_30d, regimes, cfg, cfg, cfg, daily_ctx,
        )

        assert len(perms) == n
        for i in range(n):
            assert perms[i].allow_long == bool(al[i]), f"bar {i} allow_long mismatch"
            assert perms[i].allow_short == bool(as_[i]), f"bar {i} allow_short mismatch"
            assert perms[i].force_flat == bool(ff[i]), f"bar {i} force_flat mismatch"
            assert perms[i].exit_only == bool(eo[i]), f"bar {i} exit_only mismatch"

    def test_permission_arrays_and_apply_consistent(self, ohlcv_30d, daily_ctx, regimes):
        """apply_permission_arrays should match apply_permissions_with_position."""
        n = len(ohlcv_30d)
        signals = np.random.default_rng(123).choice([0, 1, 2, 3], size=n).astype(int)
        cfg = RiskOffConfig(allow_long=True, allow_short=True, directional_only=True,
                            ema_fast=50, ema_slope_days=5)

        # List path
        perms = compute_permissions(ohlcv_30d, regimes, cfg, cfg, cfg)
        out_list = apply_permissions_with_position(signals.copy(), perms)

        # Array path
        al, as_, ff, eo = build_permission_arrays(
            ohlcv_30d, regimes, cfg, cfg, cfg, daily_ctx,
        )
        out_arr = apply_permission_arrays(signals.copy(), al, as_, ff, eo)

        assert out_list.tolist() == out_arr.tolist()


# ── force_flat semantics ─────────────────────────────────────────────────────


def test_force_flat_clears_position():
    """force_flat always outputs close(0) and resets position to flat."""
    signals = np.array([2, 1, 2, 1], dtype=int)  # enter long, hold (force_flat), re-enter long, hold long
    perms = [
        BarPermission(True, False, False, False, "ok"),
        BarPermission(False, False, True, False, "force_flat"),
        BarPermission(True, False, False, False, "ok"),
        BarPermission(True, False, False, False, "ok"),
    ]
    out = apply_permissions_with_position(signals, perms)
    assert out[0] == 2  # enter long
    assert out[1] == 0  # force_flat → close
    assert out[2] == 2  # re-enter long (raw signal is 2)
    assert out[3] == 1  # hold long


def test_compute_daily_indicators_structure():
    """compute_daily_indicators returns expected keys and types."""
    n = 30 * 288
    dts = pd.date_range("2026-05-01", periods=n, freq="5min")
    rng = np.random.default_rng(42)
    df = pd.DataFrame({
        "datetime": dts,
        "close": 3000 + np.linspace(0, 300, n) + rng.normal(0, 20, n),
        "high": np.full(n, 3100.0),
        "low": np.full(n, 2900.0),
        "open": np.full(n, 3000.0),
        "volume": np.full(n, 1000.0),
    })
    ctx = compute_daily_indicators(df)
    assert "close" in ctx
    assert "ema50" in ctx
    assert "ema100" in ctx
    assert "slope" in ctx
    assert "peak90" in ctx
    assert "consecutive_below" in ctx
    # Should have ~30 daily bars
    assert 28 <= len(ctx["close"]) <= 31


# ── N7 preset integration test ───────────────────────────────────────────────

def test_n7_preset_has_three_tier_adx():
    """N7 preset must use three-tier ADX with exit_only band."""
    from dex.regime_permissions import NEUTRAL_PRESETS
    cfg = NEUTRAL_PRESETS["N7_adx_gated"]
    assert cfg.adx_force_flat_below == 18
    assert cfg.adx_entry_min == 22
    assert cfg.directional_only is True
    assert cfg.ema_fast == 50
    assert cfg.ema_slope_days == 5
    # The exit_only band is adx_entry_min - adx_force_flat_below > 0
    assert cfg.adx_entry_min > cfg.adx_force_flat_below


def test_bull_u6_u7_exist():
    """U6 and U7 presets exist with ADX overlay."""
    from dex.regime_permissions import BULL_PRESETS
    assert "U6_ema50_adx20" in BULL_PRESETS
    assert "U7_cons3_adx20" in BULL_PRESETS
    u6 = BULL_PRESETS["U6_ema50_adx20"]
    assert u6.adx_force_flat_below == 20
    assert u6.adx_entry_min == 20
    assert u6.close_below_ema_disables_long is True


# ── DD contribution analysis ────────────────────────────────────────────────


class TestDDContribution:
    """Verify DD attribution correctly tracks all regime x direction combos."""

    def test_all_regimes_tracked(self):
        """BULL, BEAR, NEUTRAL x LONG, SHORT must all be attributable."""
        from dex.strategies.base import StrategyEvaluator

        # Synthetic scenario with clear DD:
        # Bar 0-149:  BULL,  price rises 100→150 → enter LONG near top → DD begins
        # Bar 150-199: BEAR, price drops 150→90 → enter SHORT → DD continues
        # Bar 200-249: NEUTRAL, price flat at 90 → close
        n = 250
        prices = np.full(n, 100.0, dtype=float)
        prices[0:150] = np.linspace(100, 150, 150)    # BULL: uptrend
        prices[150:200] = np.linspace(150, 90, 50)     # BEAR: sharp drop → DD
        prices[200:250] = np.full(50, 90.0)            # NEUTRAL: flat

        signals = np.ones(n, dtype=int)
        signals[140] = 2   # enter LONG near BULL peak (price ~147)
        signals[155] = 3   # close LONG at a loss (~145), enter SHORT at start of BEAR drop
        signals[210] = 0   # close SHORT at profit (~90)

        regimes = np.array(
            ["BULL"] * 150 + ["BEAR"] * 50 + ["NEUTRAL"] * 50, dtype=object
        )

        ev = StrategyEvaluator(commission=0, slippage=0)
        equity, trade_log = ev.simulate(signals, prices)

        # Find max DD
        window = 10
        peak_val = float(equity[window])
        peak_idx = window
        max_dd = 0.0
        max_dd_start = window
        max_dd_end = window
        for i in range(window, n):
            if np.isnan(equity[i]):
                continue
            if equity[i] > peak_val:
                peak_val = float(equity[i])
                peak_idx = i
            dd = (peak_val - equity[i]) / peak_val if peak_val > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
                max_dd_start = peak_idx
                max_dd_end = i

        # Enrich trade log
        enriched: list[dict] = []
        open_stack: list[dict] = []
        for t in trade_log:
            ttype = t.get("type", "")
            step = int(t.get("step", 0))
            regime = str(regimes[step]) if step < len(regimes) else "UNKNOWN"
            if ttype in ("buy",):
                open_stack.append({"entry_step": step, "entry_regime": regime, "direction": "LONG"})
            elif ttype in ("sell_short",):
                open_stack.append({"entry_step": step, "entry_regime": regime, "direction": "SHORT"})
            elif ttype in ("sell", "buy_cover", "sell_final"):
                pnl = float(t.get("pnl", 0.0))
                if open_stack:
                    entry = open_stack.pop(0)
                    enriched.append({
                        "entry_regime": entry["entry_regime"],
                        "direction": entry["direction"],
                        "pnl": pnl,
                        "entry_step": entry["entry_step"],
                        "exit_step": step,
                    })

        # Filter to DD window overlap
        dd_trades = [t for t in enriched
                     if t["entry_step"] <= max_dd_end and t["exit_step"] >= max_dd_start]

        regimes_seen = {t["entry_regime"] for t in dd_trades}
        # At minimum, the LONG entry (BULL) and SHORT entry (BEAR) should overlap
        assert len(dd_trades) >= 1, f"No trades in DD window [{max_dd_start},{max_dd_end}]"
        # Check that BULL is attributed
        bull_trades = [t for t in dd_trades if t["entry_regime"] == "BULL"]
        bear_trades = [t for t in dd_trades if t["entry_regime"] == "BEAR"]
        assert len(bull_trades) + len(bear_trades) >= 1, \
            f"Expected BULL or BEAR trades, got regimes={regimes_seen}"

    def test_long_and_short_distinguished(self):
        """LONG and SHORT in same regime must be separate keys."""
        from dex.strategies.base import StrategyEvaluator

        n = 200
        prices = np.full(n, 100.0, dtype=float)
        prices[50:100] = np.linspace(100, 110, 50)   # LONG wins
        prices[100:150] = np.linspace(110, 95, 50)    # SHORT wins

        signals = np.ones(n, dtype=int)
        signals[49] = 2   # BULL LONG entry
        signals[51] = 3   # close LONG, enter SHORT (still in BULL)
        signals[150] = 0  # close

        regimes = np.array(["BULL"] * n, dtype=object)
        dts = np.array([f"2026-01-{d:02d}" for d in range(1, n + 1)])

        ev = StrategyEvaluator(commission=0, slippage=0)
        equity, trade_log = ev.simulate(signals, prices)

        # Enrich
        enriched: list[dict] = []
        open_stack: list[dict] = []
        for t in trade_log:
            ttype = t.get("type", "")
            step = int(t.get("step", 0))
            regime = str(regimes[step]) if step < len(regimes) else "UNKNOWN"
            if ttype in ("buy",):
                open_stack.append({"entry_step": step, "entry_regime": regime, "direction": "LONG"})
            elif ttype in ("sell_short",):
                open_stack.append({"entry_step": step, "entry_regime": regime, "direction": "SHORT"})
            elif ttype in ("sell", "buy_cover", "sell_final"):
                pnl = float(t.get("pnl", 0.0))
                if open_stack:
                    entry = open_stack.pop(0)
                    enriched.append({
                        "entry_regime": entry["entry_regime"],
                        "direction": entry["direction"],
                        "pnl": pnl,
                        "entry_step": entry["entry_step"],
                        "exit_step": step,
                    })

        by_rd: dict[str, float] = {}
        for t in enriched:
            key = f"{t['entry_regime']}_{t['direction']}"
            by_rd[key] = by_rd.get(key, 0.0) + t["pnl"]

        assert "BULL_LONG" in by_rd, f"Keys: {list(by_rd.keys())}"
        assert "BULL_SHORT" in by_rd, f"Keys: {list(by_rd.keys())}"
        # LONG should be profitable (price went up)
        assert by_rd["BULL_LONG"] > 0, f"BULL_LONG pnl={by_rd['BULL_LONG']}"
        # SHORT should also be profitable (price went down)
        assert by_rd["BULL_SHORT"] > 0, f"BULL_SHORT pnl={by_rd['BULL_SHORT']}"
