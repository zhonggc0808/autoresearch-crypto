from __future__ import annotations

import pandas as pd

from scripts.analyze_market_intel_rule_attribution import write_report


def test_write_report_outputs_csv_and_markdown(tmp_path) -> None:
    df = pd.DataFrame(
        {
            "entry_step": [1, 2],
            "close_step": [2, 3],
            "entry_time": pd.to_datetime(["2026-01-01 00:00", "2026-01-01 01:00"]),
            "close_time": pd.to_datetime(["2026-01-01 00:05", "2026-01-01 01:05"]),
            "side": ["long", "short"],
            "pnl": [-1.0, 2.0],
            "holding_bars": [1, 1],
            "mode": ["cautious", "normal"],
            "funding_zscore": [-2.5, 0.5],
            "oi_change_1h": [-0.03, 0.01],
            "oi_change_4h": [-0.04, 0.02],
            "price_return_1h": [0.01, -0.01],
            "realized_vol_1h": [0.01, 0.02],
        }
    )
    md = tmp_path / "report.md"
    csv = tmp_path / "rows.csv"

    write_report(df, {"total_return": -0.1, "max_drawdown": -0.2}, md, csv)

    assert csv.exists()
    text = md.read_text(encoding="utf-8")
    assert "Market Intel Rule Attribution" in text
    assert "Side: long" in text
