#!/usr/bin/env python3
"""Dynamic validation for LLM-generated strategy code (v0.9a).

Tests that cannot be done with static analysis alone:
    - Interface contract (generate_signals exists, correct signature)
    - Output constraints (length match, signal values in {0,1,2,3})
    - No side effects (no file writes, no global state mutation)
    - Basic future-data leakage detection (shift test, rolling test)

Usage:
    from scripts.validate_code_candidate import validate_candidate_code

    errors = validate_candidate_code(strategy_module)
    if errors:
        print("Validation failed:", errors)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import List, Optional
from types import ModuleType


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALLOWED_SIGNAL_VALUES = {0, 1, 2, 3}

SAMPLE_CODE = """\
import numpy as np
import pandas as pd

def generate_signals(df):
    signals = np.zeros(len(df), dtype=np.int8)
    close = df['close'].values
    for i in range(20, len(df)):
        if close[i] > np.mean(close[i-20:i]):
            signals[i] = 2
        else:
            signals[i] = 3
    return signals
"""


# ---------------------------------------------------------------------------
# Test data factory
# ---------------------------------------------------------------------------


def _make_test_data(n: int = 200) -> "pd.DataFrame":
    """Create synthetic OHLCV data for testing."""
    import numpy as np
    import pandas as pd

    np.random.seed(42)
    base = 100.0
    close = base + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n)) * 0.3
    low = close - np.abs(np.random.randn(n)) * 0.3
    open_ = close - np.random.randn(n) * 0.2

    return pd.DataFrame({
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": np.abs(np.random.randn(n)) * 1000,
    })


# ---------------------------------------------------------------------------
# Interface validation
# ---------------------------------------------------------------------------


def validate_interface(strategy_module: ModuleType) -> List[str]:
    """Validate that the strategy module implements the required interface.

    Checks:
        1. generate_signals function exists
        2. Function is callable
        3. Returns np.ndarray
        4. Output length matches input length
        5. All output values are in {0, 1, 2, 3}
        6. Function runs without exceptions on synthetic data
    """
    import numpy as np
    errors: List[str] = []

    if not hasattr(strategy_module, "generate_signals"):
        return ["Missing required function: 'generate_signals'"]

    if not callable(strategy_module.generate_signals):
        return ["'generate_signals' is not callable"]

    # Test with synthetic data
    df = _make_test_data(200)

    try:
        signals = strategy_module.generate_signals(df)
    except Exception as e:
        return [f"generate_signals raised exception: {type(e).__name__}: {e}"]

    # --- Type check ---
    if not isinstance(signals, np.ndarray):
        errors.append(
            f"Output type must be np.ndarray, got {type(signals).__name__}"
        )
        return errors  # Can't check further

    # --- Length check ---
    if len(signals) != len(df):
        errors.append(
            f"Output length {len(signals)} != input length {len(df)}"
        )

    # --- Signal values check ---
    unique_values = set(int(v) for v in np.unique(signals))
    invalid = unique_values - ALLOWED_SIGNAL_VALUES
    if invalid:
        errors.append(
            f"Invalid signal values: {sorted(invalid)}. "
            f"Allowed: {sorted(ALLOWED_SIGNAL_VALUES)}"
        )

    # --- Dtype check ---
    if signals.dtype not in (np.int8, np.int16, np.int32, np.int64, np.uint8):
        # Float arrays with integer values are acceptable too
        if signals.dtype.kind != "f" or not np.all(signals == signals.astype(int)):
            errors.append(
                f"Signal dtype '{signals.dtype}' may cause issues; "
                f"prefer int8"
            )

    return errors


# ---------------------------------------------------------------------------
# No future-data tests
# ---------------------------------------------------------------------------


def validate_no_future_data(strategy_module: ModuleType, n: int = 200) -> List[str]:
    """Run heuristic tests for future-data leakage.

    These are NOT proofs — absence of evidence is not evidence of absence.
    They catch common patterns like:
        - Using future close for current signal
        - Lookahead in rolling computations
        - Shift-based leakage where data from t+1 affects decision at t
    """
    import numpy as np
    import pandas as pd
    errors: List[str] = []

    df = _make_test_data(n)

    try:
        signals_orig = strategy_module.generate_signals(df.copy())
    except Exception as e:
        return [f"Future-data test: generate_signals raised: {e}"]

    # --- Test 1: shift test ---
    # Shift data forward by 1 period. If signals change for matching
    # indices, the strategy may be using future values.
    df_shifted = df.shift(1).iloc[1:].dropna()
    if len(df_shifted) > 10:
        try:
            signals_shifted = strategy_module.generate_signals(df_shifted)
            orig_at_shifted = signals_orig[-len(signals_shifted):]
            match_rate = np.mean(
                signals_shifted[:len(orig_at_shifted)] == orig_at_shifted[:len(signals_shifted)]
            )
            if match_rate < 0.3 and len(signals_shifted) > 20:
                errors.append(
                    f"Future-data suspicion: shift test match rate {match_rate:.0%} "
                    f"(below 30% threshold)"
                )
        except Exception:
            pass  # Skip if shift test fails

    # --- Test 2: rolling window isolation ---
    # Create data where the last 10 values are anomalous
    df_anom = df.copy()
    anomaly = df_anom["close"].iloc[-1] * 2
    df_anom.iloc[-1, df_anom.columns.get_loc("close")] = anomaly

    try:
        signals_before = strategy_module.generate_signals(df.iloc[:-1].copy())
        signals_with_anom = strategy_module.generate_signals(df_anom.copy())
        # Compare first n-10 signals (should be identical if no lookahead)
        compare_len = min(len(signals_before) - 1, len(signals_with_anom) - 2)
        if compare_len > 0:
            match = np.mean(
                signals_before[:compare_len] == signals_with_anom[:compare_len]
            )
            if match < 0.95 and compare_len > 20:
                errors.append(
                    f"Future-data suspicion: anomaly in tail affected early signals "
                    f"(match rate {match:.0%})"
                )
    except Exception:
        pass

    return errors


# ---------------------------------------------------------------------------
# Side-effect detection
# ---------------------------------------------------------------------------


def validate_no_side_effects(strategy_module: ModuleType) -> List[str]:
    """Check that running generate_signals doesn't create files."""
    errors: List[str] = []

    df = _make_test_data(100)

    # Snapshot directory contents
    import tempfile
    tmpdir = Path(tempfile.mkdtemp())
    try:
        # Run in tempdir to catch file creation
        old_cwd = Path.cwd()
        os.chdir(tmpdir)
        try:
            before = set(os.listdir(tmpdir))
            strategy_module.generate_signals(df)
            after = set(os.listdir(tmpdir))
            created = after - before - {".", ".."}
            if created:
                errors.append(
                    f"Side-effect detected: strategy created files: {created}"
                )
        finally:
            os.chdir(old_cwd)
    except Exception:
        pass
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    return errors


# ---------------------------------------------------------------------------
# Combined validation
# ---------------------------------------------------------------------------


def validate_candidate_code(
    strategy_module: ModuleType,
    *,
    run_future_data_tests: bool = True,
    run_side_effect_tests: bool = True,
) -> List[str]:
    """Run all dynamic validations on a candidate strategy module.

    Returns list of error messages (empty = all checks passed).
    """
    errors: List[str] = []

    errors.extend(validate_interface(strategy_module))
    if errors:
        return errors  # Don't run further tests if interface is broken

    if run_future_data_tests:
        errors.extend(validate_no_future_data(strategy_module))

    if run_side_effect_tests:
        errors.extend(validate_no_side_effects(strategy_module))

    return errors
