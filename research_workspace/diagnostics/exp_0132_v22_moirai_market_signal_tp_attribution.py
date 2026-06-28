from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

EXP0110_PATH = PROJECT_ROOT / "research_workspace/diagnostics/exp_0110_v22_moirai_reversal_confirmation_delay.py"
EXP0131 = PROJECT_ROOT / "research_workspace/diagnostics/exp_0131_v22_moirai_market_signal_tp"
OUT = PROJECT_ROOT / "research_workspace/diagnostics/exp_0132_v22_moirai_market_signal_tp_attribution"


def pct(x: float | None) -> str:
    return "" if x is None or pd.isna(x) else f"{x * 100:.2f}%"


def money(x: float | None) -> str:
    return "" if x is None or pd.isna(x) else f"{x:.2f}"


def load_base_trade_notional() -> dict[int, float]:
    spec = importlib.util.spec_from_file_location("exp0110_helper", EXP0110_PATH)
    exp0110 = importlib.util.module_from_spec(spec)
    sys.modules["exp0110_helper"] = exp0110
    assert spec.loader is not None
    spec.loader.exec_module(exp0110)
    df, signals, _ = exp0110.load_base()
    _, trades = exp0110.helper0108.next_open_trades(signals, df)
    return {int(t["entry_step"]): float(t.get("entry_notional") or np.nan) for t in trades}


def classify_exit(base_pnl: float, early_exit_pnl_proxy: float) -> str:
    delta = early_exit_pnl_proxy - base_pnl
    if base_pnl <= 0 and delta > 0:
        return "saved_loser"
    if base_pnl > 0 and delta > 0:
        return "improved_winner"
    if base_pnl > 0:
        return "missed_winner"
    return "worsened_loser"


def classify_variant(row: pd.Series) -> tuple[str, str]:
    pass_gate = bool(row.get("pass_gate", False))
    live_hit = bool(row.get("live_hit", False))
    exits = int(row.get("exits", 0) or 0)
    top20 = int(row.get("top20_winner_exits", 0) or 0)
    delta_oos = float(row.get("delta_oos", 0.0) or 0.0)
    rolling12 = float(row.get("rolling12_min", 0.0) or 0.0)
    if pass_gate and live_hit:
        return "A_plus_live_candidate", "OBSERVE_REVIEW_MANUALLY"
    if pass_gate and not live_hit:
        return "A_long_pass_no_live", "OBSERVE_REVIEW_EXITS"
    if live_hit and not pass_gate:
        return "B_live_hit_rejected_for_live", "REJECTED_FOR_LIVE"
    if exits >= 20 or top20 >= 3 or delta_oos <= -0.50 or rolling12 < 0:
        return "C_overtrigger_or_winner_damage", "REJECT"
    return "D_low_signal_or_neutral", "REJECT_OR_IGNORE"


def enrich_exits(exits: pd.DataFrame, notional_by_entry: dict[int, float]) -> pd.DataFrame:
    out = exits.copy()
    if out.empty:
        return out
    out["entry_notional"] = out["base_trade_entry_bar"].map(notional_by_entry)
    out["early_exit_pnl_proxy"] = out["current_return"].astype(float) * out["entry_notional"].astype(float)
    out["delta_vs_base_pnl_proxy"] = out["early_exit_pnl_proxy"] - out["base_trade_pnl"].astype(float)
    out["exit_quality"] = [
        classify_exit(float(base), float(early))
        for base, early in zip(out["base_trade_pnl"], out["early_exit_pnl_proxy"], strict=False)
    ]
    out["saved_pnl_proxy"] = np.where(out["delta_vs_base_pnl_proxy"] > 0, out["delta_vs_base_pnl_proxy"], 0.0)
    out["missed_pnl_proxy"] = np.where(out["delta_vs_base_pnl_proxy"] < 0, -out["delta_vs_base_pnl_proxy"], 0.0)
    return out


def summarize_variants(matrix: pd.DataFrame, enriched: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    live_view = live[
        ["variant", "status", "trigger_time_cst", "close", "current_return", "before_1590", "reasons"]
    ].copy()
    live_view = live_view.rename(
        columns={
            "status": "live_status",
            "trigger_time_cst": "live_trigger_time_cst",
            "close": "live_trigger_close",
            "current_return": "live_current_return",
            "before_1590": "live_before_1590",
            "reasons": "live_reasons",
        }
    )
    summary = matrix.merge(live_view, on="variant", how="left")
    summary["live_hit"] = summary["live_status"].eq("triggered")

    if not enriched.empty:
        grouped = enriched.groupby("variant", dropna=False)
        attr = grouped.agg(
            exit_rows=("variant", "size"),
            saved_loser_count=("exit_quality", lambda s: int((s == "saved_loser").sum())),
            improved_winner_count=("exit_quality", lambda s: int((s == "improved_winner").sum())),
            missed_winner_count=("exit_quality", lambda s: int((s == "missed_winner").sum())),
            worsened_loser_count=("exit_quality", lambda s: int((s == "worsened_loser").sum())),
            valuable_exit_count=("delta_vs_base_pnl_proxy", lambda s: int((s > 0).sum())),
            net_delta_pnl_proxy=("delta_vs_base_pnl_proxy", "sum"),
            saved_pnl_proxy=("saved_pnl_proxy", "sum"),
            missed_pnl_proxy=("missed_pnl_proxy", "sum"),
            avg_delta_pnl_proxy=("delta_vs_base_pnl_proxy", "mean"),
        ).reset_index()
        summary = summary.merge(attr, on="variant", how="left")

    for col in [
        "exit_rows",
        "saved_loser_count",
        "improved_winner_count",
        "missed_winner_count",
        "worsened_loser_count",
        "valuable_exit_count",
    ]:
        summary[col] = summary[col].fillna(0).astype(int)
    for col in ["net_delta_pnl_proxy", "saved_pnl_proxy", "missed_pnl_proxy", "avg_delta_pnl_proxy"]:
        summary[col] = summary[col].fillna(0.0).astype(float)

    categories = summary.apply(classify_variant, axis=1, result_type="expand")
    summary["compression_category"] = categories[0]
    summary["recommendation"] = categories[1]
    return summary.sort_values(
        ["compression_category", "pass_gate", "live_hit", "delta_oos"],
        ascending=[True, False, False, False],
    )


def write_csv(path: Path, rows: pd.DataFrame) -> None:
    rows.to_csv(path, index=False, quoting=csv.QUOTE_MINIMAL)


def write_report(summary: pd.DataFrame, enriched: pd.DataFrame) -> None:
    counts = summary["compression_category"].value_counts().to_dict()
    a = summary[summary["compression_category"] == "A_long_pass_no_live"].copy()
    b = summary[summary["compression_category"] == "B_live_hit_rejected_for_live"].copy()
    c = summary[summary["compression_category"] == "C_overtrigger_or_winner_damage"].copy()

    representative_a = "1h_strict_engulf_v2p0_macd4h"
    rep_exits = enriched[enriched["variant"] == representative_a].copy()
    rep_row = summary[summary["variant"] == representative_a].iloc[0].to_dict()

    live_repair = "2h_wick_r75_v2p0_then_macd2h_12h"
    live_row = summary[summary["variant"] == live_repair].iloc[0].to_dict()

    md = [
        "# exp_0132 market-signal TP attribution compression",
        "",
        "- input: `exp_0131` matrix/exits/live-case outputs",
        "- purpose: compress 86 variants into A/B/C action buckets before any further search",
        "- attribution proxy: `early_exit_pnl_proxy = current_return_at_trigger * base_entry_notional`; compare against baseline trade final PnL",
        "- live/checkpoint/config action: no change",
        "",
        "## Bucket Counts",
        "",
        "| category | count | action |",
        "|---|---:|---|",
        f"| A long pass, no live-case hit | {counts.get('A_long_pass_no_live', 0)} | OBSERVE; inspect exits only |",
        f"| B live-case hit, failed long-window gate | {counts.get('B_live_hit_rejected_for_live', 0)} | rejected_for_live; explanation only |",
        f"| C overtrigger or winner damage | {counts.get('C_overtrigger_or_winner_damage', 0)} | REJECT/archive |",
        f"| D low-signal or neutral rejects | {counts.get('D_low_signal_or_neutral', 0)} | ignore unless new evidence |",
        "",
        "## A. Long-Window Pass But No Live-Case Hit",
        "",
        "| variant | exits | dOOS | DD improve | top20 cut | valuable exits | missed winners | net delta proxy | action |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in a.head(12).to_dict("records"):
        md.append(
            f"| {row['variant']} | {int(row['exits'])} | {pct(row['delta_oos'])} | {pct(row['dd_improve_rel'])} | "
            f"{int(row['top20_winner_exits'])} | {int(row['valuable_exit_count'])} | "
            f"{int(row['missed_winner_count'])} | {money(row['net_delta_pnl_proxy'])} | {row['recommendation']} |"
        )

    md.extend(
        [
            "",
            f"Representative `{representative_a}`:",
            "",
            f"- exits: `{int(rep_row['exits'])}`",
            f"- valuable exits by proxy: `{int(rep_row['valuable_exit_count'])}`",
            f"- missed winners by proxy: `{int(rep_row['missed_winner_count'])}`",
            f"- saved losers: `{int(rep_row['saved_loser_count'])}`",
            f"- net delta proxy vs baseline trades: `{money(rep_row['net_delta_pnl_proxy'])}`",
            "- read: long-window metrics are interesting, but the six exits are mostly winner cuts rather than clear saved losers.",
            "",
            "Representative exits:",
            "",
            "| time | side | base pnl | early pnl proxy | delta proxy | quality | top20 | reasons |",
            "|---|---|---:|---:|---:|---|---:|---|",
        ]
    )
    for row in rep_exits.sort_values("time").to_dict("records"):
        md.append(
            f"| {row['time']} | {row['side']} | {money(row['base_trade_pnl'])} | "
            f"{money(row['early_exit_pnl_proxy'])} | {money(row['delta_vs_base_pnl_proxy'])} | "
            f"{row['exit_quality']} | {bool(row['base_trade_top20_winner'])} | {row['reasons']} |"
        )

    md.extend(
        [
            "",
            "## B. Live-Case Hit But Long-Window Gate Failed",
            "",
            "| variant | live trigger | close | dOOS | DD improve | roll12 | exits | top20 cut | year W/L/F | net delta proxy | action |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in b.head(20).to_dict("records"):
        md.append(
            f"| {row['variant']} | {row.get('live_trigger_time_cst', '')} | {money(row.get('live_trigger_close'))} | "
            f"{pct(row['delta_oos'])} | {pct(row['dd_improve_rel'])} | {pct(row['rolling12_min'])} | "
            f"{int(row['exits'])} | {int(row['top20_winner_exits'])} | "
            f"{int(row['year_wins'])}/{int(row['year_losses'])}/{int(row['year_flat'])} | "
            f"{money(row['net_delta_pnl_proxy'])} | {row['recommendation']} |"
        )

    md.extend(
        [
            "",
            f"Representative `{live_repair}`:",
            "",
            f"- would have exited the 2026-06-23 Bitget short at `{live_row.get('live_trigger_time_cst')}` near `{money(live_row.get('live_trigger_close'))}`",
            f"- current-return-at-trigger was `{pct(live_row.get('live_current_return'))}` in the live-case CSV",
            f"- long-window top20 cuts: `{int(live_row['top20_winner_exits'])}`",
            f"- year W/L/F: `{int(live_row['year_wins'])}/{int(live_row['year_losses'])}/{int(live_row['year_flat'])}`",
            "- read: useful for explaining the live short, not suitable as a strategy candidate.",
            "",
            "## C. Overtrigger Or Top-Winner Damage",
            "",
            "| variant | exits | dOOS | roll12 | top20 cut | live hit | action |",
            "|---|---:|---:|---:|---:|---:|---|",
        ]
    )
    for row in c.sort_values(["top20_winner_exits", "exits"], ascending=False).head(25).to_dict("records"):
        md.append(
            f"| {row['variant']} | {int(row['exits'])} | {pct(row['delta_oos'])} | {pct(row['rolling12_min'])} | "
            f"{int(row['top20_winner_exits'])} | {bool(row['live_hit'])} | {row['recommendation']} |"
        )

    md.extend(
        [
            "",
            "## Conclusion",
            "",
            "- Do not expand the wick/engulf/MACD matrix further.",
            "- Keep A variants as OBSERVE only; their exit-level evidence is not yet strong enough to promote.",
            "- Mark B variants as `rejected_for_live`: they explain the 2026-06-23 short but fail long-window winner-damage controls.",
            "- Archive C variants as REJECT, especially r60 wick and loose/fast MACD families with high top20 cuts.",
            "- If market-signal TP is revisited, use a new direction: arm the market signal only after unusually profitable trades, such as ATR-normalized open profit or entry-risk multiple, rather than adding more shape combinations.",
            "",
            f"Summary CSV: `{OUT.with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
            f"Exit attribution CSV: `{OUT.with_name(OUT.name + '_exit_attribution').with_suffix('.csv').relative_to(PROJECT_ROOT)}`",
        ]
    )
    OUT.with_suffix(".md").write_text("\n".join(md) + "\n", encoding="utf-8")


def main() -> None:
    matrix_path = EXP0131.with_suffix(".csv")
    exits_path = EXP0131.with_name(EXP0131.name + "_exits").with_suffix(".csv")
    live_path = EXP0131.with_name(EXP0131.name + "_live_case").with_suffix(".csv")
    for path in (matrix_path, exits_path, live_path):
        if not path.exists():
            raise SystemExit(f"missing input: {path}")

    matrix = pd.read_csv(matrix_path)
    exits = pd.read_csv(exits_path)
    live = pd.read_csv(live_path)
    notional_by_entry = load_base_trade_notional()
    enriched = enrich_exits(exits, notional_by_entry)
    summary = summarize_variants(matrix, enriched, live)

    write_csv(OUT.with_suffix(".csv"), summary)
    write_csv(OUT.with_name(OUT.name + "_exit_attribution").with_suffix(".csv"), enriched)
    report = {
        "scope": {
            "experiment_id": "exp_0132",
            "source": "exp_0131_v22_moirai_market_signal_tp",
            "mode": "attribution compression only",
            "live_action": "no_change",
            "checkpoint_action": "no_change",
        },
        "category_counts": summary["compression_category"].value_counts().to_dict(),
        "representatives": {
            "A": "1h_strict_engulf_v2p0_macd4h",
            "B": "2h_wick_r75_v2p0_then_macd2h_12h",
        },
        "summary": summary.to_dict("records"),
    }
    OUT.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(summary, enriched)
    print(OUT.with_suffix(".md"))
    print(summary["compression_category"].value_counts().to_string())
    for row in summary.head(12).to_dict("records"):
        print(row["variant"], row["compression_category"], row["recommendation"], "net", round(row["net_delta_pnl_proxy"], 2))


if __name__ == "__main__":
    main()
