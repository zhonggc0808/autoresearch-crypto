"""
GEPA Reflective Evolution runner.

Combines the ATLAS multi-agent system with the GEPA reflection engine.
Each experiment is hypothesis-driven, with auto-generated reflections
and periodic meta-analysis.

Usage:
    uv run python scripts/evolve_gepa.py
    uv run python scripts/evolve_gepa.py --cycles 20 --days 60
"""

from __future__ import annotations

import argparse
import inspect
import json
import os
import sys
import warnings
from datetime import datetime
from typing import Dict, Tuple

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings("ignore")

# Fix Windows GBK encoding issues
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dex.config import DATA_DIR  # noqa: E402 — sys.path must be set first
from dex.evolution import create_default_agents  # noqa: E402
from dex.llm_hypothesis import LLMHypothesisGenerator  # noqa: E402
from dex.reflection import ReflectionEngine, gepa_evolve, gepa_evolve_v2  # noqa: E402
from dex.strategies.base import StrategyEvaluator  # noqa: E402
from dex.strategies.grid import grid_signals_to_discrete  # noqa: E402


def make_evaluate_fn(df: pd.DataFrame):
    """Build an evaluate_fn closure for the GEPA engine.

    Returns a function (params) -> (score, sharpe, return, max_dd).
    """
    evaluator = StrategyEvaluator()
    # Use full dataset for evaluation — a small validation slice in a bear
    # market produces false-negative feedback that blocks all parameter changes.
    # Market-relative scoring already penalises overfitting via the trade-count
    # component and the drawdown guard.
    val_df = df.reset_index(drop=True)
    val_prices = val_df["close"].values.astype(float)

    def _relaxed_score(signals, prices, df_slice):
        equity, trades = evaluator.simulate(signals, prices, df_slice)
        if len(equity) == 0 or not np.all(np.isfinite(equity)):
            return 0.0, 0.0, 0.0, -0.99
        metrics = evaluator.compute_metrics(equity, trades)
        ret = metrics["total_return"]
        dd = metrics["max_drawdown"]
        sharpe = max(-3.0, min(5.0, metrics["sharpe_ratio"]))
        wr = metrics["win_rate"]
        trade_pnls = [t for t in trades if t.get("pnl") is not None]
        n_trades = len(trade_pnls)
        if ret <= -0.90 or dd < -0.80 or n_trades < 1:
            return 0.0, sharpe, ret, dd
        market_return = (prices[-1] / prices[0] - 1) if prices[0] > 0 else 0.0
        excess = ret - market_return
        excess_c = max(-0.50, min(2.0, excess))
        ret_score = max(0, min(1.0, (excess_c + 0.10) / 0.30))
        sharpe_score = max(0, min(1.0, (sharpe + 1.0) / 4.0))
        dd_score = max(0, min(1.0, 1.0 - abs(dd) / 0.50))
        wr_score = max(0, min(1.0, (wr - 0.35) / 0.30))
        trade_score = min(1.0, n_trades / 20.0)
        score = (
            ret_score * 0.30
            + sharpe_score * 0.20
            + dd_score * 0.20
            + wr_score * 0.15
            + trade_score * 0.15
        )
        return score, sharpe, ret, dd

    def evaluate_fn(params: Dict) -> Tuple[float, float, float, float]:
        """Evaluate params -> (score, sharpe, return, max_dd)."""
        # Need strategy_cls — we determine it by checking which keys are present
        # This is called per-agent, so we need the agent's class
        # For now, try TrendStrategy first, then others
        from dex.strategies.grid import GridStrategy
        from dex.strategies.hybrid_mm import HybridMeanRevMomentumStrategy
        from dex.strategies.pure_action import PureActionStrategy
        from dex.strategies.trend import TrendStrategy

        # Determine class from params
        if "grid_spacing_pct" in params:
            cls = GridStrategy
        elif "rsi_low" in params and "rsi_high" in params:
            cls = HybridMeanRevMomentumStrategy
        elif "trend_ma_period" in params and "adx_threshold" in params:
            cls = PureActionStrategy
        else:
            cls = TrendStrategy

        sig_params = inspect.signature(cls.__init__).parameters.keys()
        valid_params = {k: v for k, v in params.items() if k in sig_params}

        strategy = cls(**valid_params)
        signals = strategy.generate_signals(val_df)

        if signals.dtype in (np.float64, np.float32, float):
            signals = grid_signals_to_discrete(signals, val_prices)

        min_start = getattr(strategy, "window", 20) * 2
        return _relaxed_score(
            signals[min_start:],
            val_prices[min_start:],
            val_df.iloc[min_start:].reset_index(drop=True),
        )

    return evaluate_fn


def main():
    parser = argparse.ArgumentParser(description="GEPA Reflective Evolution")
    parser.add_argument("--cycles", type=int, default=15)
    parser.add_argument("--days", type=int, default=60)
    parser.add_argument("--data", type=str, default=None)
    parser.add_argument("--v2", action="store_true", help="Use GEPA V2 (risk-adjusted scoring + edge guards + revival)")
    parser.add_argument("--llm", action="store_true", help="Use LLM to generate dynamic hypotheses (set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY)")
    parser.add_argument("--llm-model", type=str, default="deepseek-v4-flash", help="LLM model ID (default: deepseek-v4-flash)")
    parser.add_argument("--llm-provider", type=str, default="auto", choices=["auto", "deepseek", "anthropic"], help="LLM provider (default: auto-detect from model name)")
    args = parser.parse_args()

    # Load data
    data_path = args.data or os.path.join(str(DATA_DIR), "ETHUSDT_5m_60d.parquet")
    if not os.path.exists(data_path):
        print(f"Error: {data_path} not found")
        sys.exit(1)

    table = pq.read_table(data_path)
    df = table.to_pandas()
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    df = df.iloc[-288 * args.days :].reset_index(drop=True)

    print(f"Data: {len(df)} bars ({args.days} days)")
    print(
        f"Price: {df['close'].iloc[0]:.1f} -> {df['close'].iloc[-1]:.1f} "
        f"({(df['close'].iloc[-1] / df['close'].iloc[0] - 1) * 100:+.2f}%)"
    )

    # Create agents and engines
    agents = create_default_agents()
    evaluate_fn = make_evaluate_fn(df)
    reflection = ReflectionEngine()

    # LLM hypothesis generator (optional)
    llm_gen = None
    if args.llm:
        # Resolve API key: try provider-specific env first, then generic
        api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("LLM_API_KEY", "")
        if not api_key:
            print("Warning: No API key found (set DEEPSEEK_API_KEY or ANTHROPIC_API_KEY), LLM disabled")
        else:
            llm_gen = LLMHypothesisGenerator(
                api_key=api_key,
                model=args.llm_model,
                provider=args.llm_provider,
            )
            print(f"[LLM] 启用 LLM 假设生成: {llm_gen.provider}/{args.llm_model}")

    # Build on_revive callback for data-switching revival actions
    def _on_revive(action: str, action_params: Dict) -> tuple:
        """Handle timeframe and symbol switching during revival.

        Returns (new_evaluate_fn, new_df) or (None, None).
        """
        nonlocal evaluate_fn, df
        if action == "switch_timeframe":
            tfs = action_params.get("timeframes", ["15m", "1h", "4h"])
            # Pick first available timeframe
            tf_map = {
                "15m": "15min", "1h": "1h", "4h": "4h",
            }
            for tf in tfs:
                rule = tf_map.get(tf, "15min")
                try:
                    df_resampled = df.set_index("timestamp").resample(rule).agg({
                        "open": "first", "high": "max", "low": "min",
                        "close": "last", "volume": "sum",
                    }).dropna().reset_index()
                    if len(df_resampled) >= 100:
                        df = df_resampled
                        evaluate_fn = make_evaluate_fn(df)
                        print(f"  [Revive] 数据切换至 {tf}，{len(df)} bars")
                        return evaluate_fn, df
                except Exception as e:
                    print(f"  [Revive] 时间框架切换失败 ({tf}): {e}")
                    continue
            return None, None

        elif action == "switch_symbol":
            symbols = action_params.get("symbols", ["BTCUSDT", "SOLUSDT"])
            for sym in symbols:
                data_path = os.path.join(str(DATA_DIR), f"{sym}_5m_60d.parquet")
                if not os.path.exists(data_path):
                    print(f"  [Revive] 数据文件不存在: {data_path}")
                    continue
                try:
                    table = pq.read_table(data_path)
                    new_df = table.to_pandas()
                    for col in ["open", "high", "low", "close", "volume"]:
                        if col in new_df.columns:
                            new_df[col] = new_df[col].astype(float)
                    new_df = new_df.iloc[-288 * args.days:].reset_index(drop=True)
                    if len(new_df) >= 100:
                        df = new_df
                        evaluate_fn = make_evaluate_fn(df)
                        print(f"  [Revive] 币种切换至 {sym}，价格 {df['close'].iloc[0]:.1f}，{len(df)} bars")
                        return evaluate_fn, df
                except Exception as e:
                    print(f"  [Revive] 币种切换失败 ({sym}): {e}")
                    continue
            return None, None

        return None, None

    # Run GEPA evolution
    if args.v2:
        engine = gepa_evolve_v2(
            agents=agents,
            evaluate_fn_raw=evaluate_fn,
            engine=reflection,
            cycles=args.cycles,
            min_trades=10,
            max_dd=0.30,
            dead_threshold=5,
            verbose=True,
            llm_generator=llm_gen,
            on_revive=_on_revive,
        )
    else:
        engine = gepa_evolve(
            agents=agents,
            evaluate_fn=evaluate_fn,
            engine=reflection,
            cycles=args.cycles,
            verbose=True,
        )

    # Final report
    print("\n" + "=" * 60)
    print("最终参数与反思摘要")
    print("=" * 60)
    for agent in agents:
        score, sharpe, ret, dd = evaluate_fn(agent.params)
        print(f"\n{agent.name} ({agent.style}):")
        print(
            f"  Score={score:.4f}  Sharpe={sharpe:.2f}  "
            f"Return={ret * 100:+.2f}%  DD={dd * 100:.1f}%"
        )
        print(f"  Params: {json.dumps(agent.params, indent=2, default=str)[:200]}")

    print(f"\n总实验数: {len(engine.experiment_logs)}")
    print(f"元反思数: {len(engine.meta_reflections)}")
    print(f"发现盲点: {len(engine.blind_spots)}")
    if engine.blind_spots:
        print("  最新盲点:")
        for bs in engine.blind_spots[-3:]:
            print(f"    - {bs}")

    # Save
    out = {
        "timestamp": datetime.now().isoformat(),
        "agents": [
            {
                "name": a.name,
                "style": a.style,
                "params": a.params,
                "score": evaluate_fn(a.params)[0],
            }
            for a in agents
        ],
        "n_experiments": len(engine.experiment_logs),
        "n_meta": len(engine.meta_reflections),
        "blind_spots": engine.blind_spots[-5:],
    }
    out_path = os.path.join(os.path.dirname(__file__), "..", "search_results", "gepa_result.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果已保存: {out_path}")


if __name__ == "__main__":
    main()
