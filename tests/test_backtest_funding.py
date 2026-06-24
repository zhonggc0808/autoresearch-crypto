from __future__ import annotations

import pandas as pd

from backtest_quant import _find_funding_file, add_funding_events


def test_add_funding_events_uses_rate_changes_once(tmp_path) -> None:
    funding_path = tmp_path / "funding.csv"
    pd.DataFrame(
        {
            "available_at": ["2026-01-01 00:30:00", "2026-01-01 01:30:00"],
            "funding_rate": [0.01, 0.01],
        }
    ).to_csv(funding_path, index=False)
    bars = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                ["2026-01-01 00:00:00", "2026-01-01 01:00:00", "2026-01-01 02:00:00"]
            ),
            "timestamp": [1, 2, 3],
        }
    )

    out, count = add_funding_events(bars, funding_path)

    assert count == 1
    assert out["funding_rate"].tolist() == [0.0, 0.01, 0.0]


def test_find_funding_file_prefers_matching_days(tmp_path) -> None:
    for days in [25, 730, 1300, 2600]:
        (tmp_path / f"ETHUSDT_1h_derivatives_{days}d.parquet").touch()

    assert _find_funding_file("ETHUSDT", 1300, root=tmp_path).name.endswith("1300d.parquet")
    assert _find_funding_file("ETHUSDT", 2000, root=tmp_path).name.endswith("2600d.parquet")
