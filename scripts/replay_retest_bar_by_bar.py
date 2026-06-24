"""CLI wrapper for ChannelBreakout retest pending-state replay."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dex.checkpoints import build_channel_breakout_strategy_from_checkpoint, load_checkpoint
from dex.live.retest_replay import replay_retest_bar_by_bar


def _normalize_datetime(df: pd.DataFrame) -> pd.DataFrame:
    if "datetime" not in df.columns and "timestamp" in df.columns:
        unit = "ms" if float(df["timestamp"].iloc[-1]) > 10_000_000_000 else "s"
        df = df.copy()
        df["datetime"] = pd.to_datetime(df["timestamp"], unit=unit)
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay retest pending state bar by bar")
    parser.add_argument("--data", required=True)
    parser.add_argument(
        "--checkpoint",
        default="checkpoints/channel_breakout_v2_2_m375_bbm375_1p5_retest_w96_tol50bp_nextopen.json",
    )
    parser.add_argument("--regime", choices=["bull", "bear", "neutral"], default="bear")
    parser.add_argument("--out", default="research_workspace/replay/retest_bar_by_bar_log.csv")
    parser.add_argument("--start")
    parser.add_argument("--end")
    args = parser.parse_args()

    df = _normalize_datetime(pd.read_parquet(args.data)).sort_values("datetime").reset_index(drop=True)
    checkpoint = load_checkpoint(args.checkpoint)
    strategy = build_channel_breakout_strategy_from_checkpoint(checkpoint, args.regime)
    log = replay_retest_bar_by_bar(df, strategy)
    if args.start:
        log = log[pd.to_datetime(log["timestamp"]) >= pd.Timestamp(args.start)]
    if args.end:
        log = log[pd.to_datetime(log["timestamp"]) <= pd.Timestamp(args.end)]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    log.to_csv(out, index=False)
    print(f"wrote {len(log)} rows to {out}")


if __name__ == "__main__":
    main()
