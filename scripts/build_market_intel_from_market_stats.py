#!/usr/bin/env python3
"""Build a frozen hourly market-intel table from local market stats."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dex.config import DATA_DIR, PROJECT_DIR
from dex.data import list_crypto_files, load_crypto_data


def build_market_intel(
    df: pd.DataFrame,
    symbol: str,
    derivatives: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return hourly market-intel rows using only completed OHLCV bars."""
    source = df.copy()
    source["datetime"] = _datetimes(source)
    source = source.sort_values("datetime").drop_duplicates(subset="datetime")
    source["bar_return"] = source["close"].astype(float).pct_change().fillna(0.0)

    hourly = (
        source.set_index("datetime")
        .resample("1h", label="right", closed="right")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
            volatility_1h=("bar_return", "std"),
        )
        .dropna(subset=["open", "high", "low", "close"])
    )
    hourly["return_1h"] = hourly["close"].pct_change().fillna(0.0)
    hourly["return_4h"] = hourly["close"].pct_change(4).fillna(0.0)
    hourly["range_1h"] = (hourly["high"] - hourly["low"]) / hourly["open"]
    hourly["volume_ratio_24h"] = (
        hourly["volume"] / hourly["volume"].rolling(24, min_periods=6).median()
    )
    hourly["volatility_ratio_24h"] = (
        hourly["volatility_1h"] / hourly["volatility_1h"].rolling(24, min_periods=6).median()
    )
    hourly = hourly.replace([np.inf, -np.inf], np.nan).fillna(1.0)

    if derivatives is not None and not derivatives.empty:
        hourly = hourly.join(_hourly_derivatives(derivatives), how="left")
    else:
        hourly["funding_rate"] = np.nan
        hourly["open_interest"] = np.nan
    hourly["funding_zscore"] = _zscore(hourly["funding_rate"], 168)
    hourly["oi_change_1h"] = (
        hourly["open_interest"].pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    )
    hourly["oi_change_4h"] = (
        hourly["open_interest"].pct_change(4, fill_method=None).replace([np.inf, -np.inf], np.nan)
    )
    hourly["oi_zscore"] = _zscore(hourly["open_interest"], 168)
    hourly[
        [
            "funding_zscore",
            "oi_change_1h",
            "oi_change_4h",
            "oi_zscore",
        ]
    ] = hourly[
        [
            "funding_zscore",
            "oi_change_1h",
            "oi_change_4h",
            "oi_zscore",
        ]
    ].fillna(0.0)

    modes: list[str] = []
    reasons: list[str] = []
    for row in hourly.itertuples():
        mode, reason = _classify(row)
        modes.append(mode)
        reasons.append(reason)

    return pd.DataFrame(
        {
            "available_at": hourly.index,
            "symbol": symbol.upper(),
            "mode": modes,
            "return_1h": hourly["return_1h"].to_numpy(),
            "price_return_1h": hourly["return_1h"].to_numpy(),
            "return_4h": hourly["return_4h"].to_numpy(),
            "range_1h": hourly["range_1h"].to_numpy(),
            "volatility_1h": hourly["volatility_1h"].to_numpy(),
            "realized_vol_1h": hourly["volatility_1h"].to_numpy(),
            "funding_rate": hourly["funding_rate"].to_numpy(),
            "funding_zscore": hourly["funding_zscore"].to_numpy(),
            "open_interest": hourly["open_interest"].to_numpy(),
            "oi_change_1h": hourly["oi_change_1h"].to_numpy(),
            "oi_change_4h": hourly["oi_change_4h"].to_numpy(),
            "oi_zscore": hourly["oi_zscore"].to_numpy(),
            "volume_ratio_24h": hourly["volume_ratio_24h"].to_numpy(),
            "volatility_ratio_24h": hourly["volatility_ratio_24h"].to_numpy(),
            "reason": reasons,
        }
    )


def _classify(row) -> tuple[str, str]:
    checks = {
        "ret1h": abs(row.return_1h),
        "ret4h": abs(row.return_4h),
        "range1h": row.range_1h,
        "vol_ratio": row.volatility_ratio_24h,
        "volume_ratio": row.volume_ratio_24h,
        "funding": abs(row.funding_rate) if pd.notna(row.funding_rate) else 0.0,
        "funding_z": abs(row.funding_zscore),
        "oi1h": row.oi_change_1h,
        "oi4h": row.oi_change_4h,
        "oi_z": row.oi_zscore,
    }
    derivatives_block = (
        checks["funding"] >= 0.0015
        or (checks["oi1h"] >= 0.05 and (checks["ret1h"] >= 0.025 or checks["vol_ratio"] >= 2.5))
        or (checks["oi4h"] >= 0.10 and (checks["ret4h"] >= 0.05 or checks["vol_ratio"] >= 2.5))
    )
    if derivatives_block:
        return "block_new_entries", _reason("extreme", checks)
    if (
        checks["ret1h"] >= 0.018
        or checks["ret4h"] >= 0.04
        or checks["range1h"] >= 0.03
        or checks["vol_ratio"] >= 2.0
        or checks["volume_ratio"] >= 2.5
        or checks["funding"] >= 0.0007
        or checks["funding_z"] >= 2.0
        or checks["oi1h"] >= 0.025
        or checks["oi4h"] >= 0.06
        or checks["oi_z"] >= 2.0
    ):
        return "cautious", _reason("elevated", checks)
    return "normal", _reason("normal", checks)


def _reason(prefix: str, checks: dict[str, float]) -> str:
    parts = ", ".join(f"{k}={v:.4f}" for k, v in checks.items())
    return f"{prefix}: {parts}"


def _datetimes(df: pd.DataFrame) -> pd.Series:
    if "datetime" in df.columns:
        return pd.to_datetime(df["datetime"], errors="coerce")
    if "timestamp" not in df.columns:
        raise ValueError("OHLCV data must contain datetime or timestamp")
    raw = df["timestamp"]
    if pd.api.types.is_numeric_dtype(raw):
        max_abs = float(np.nanmax(np.abs(raw.to_numpy(dtype=float)))) if len(raw) else 0.0
        unit = "ms" if max_abs > 10_000_000_000 else "s"
        return pd.to_datetime(raw, unit=unit, errors="coerce")
    return pd.to_datetime(raw, errors="coerce")


def _zscore(series: pd.Series, window: int) -> pd.Series:
    mean = series.rolling(window, min_periods=max(6, window // 6)).mean()
    std = series.rolling(window, min_periods=max(6, window // 6)).std()
    return ((series - mean) / std.replace(0, np.nan)).replace([np.inf, -np.inf], np.nan)


def _hourly_derivatives(df: pd.DataFrame) -> pd.DataFrame:
    data = df.copy()
    data["available_at"] = pd.to_datetime(data["available_at"], errors="coerce")
    if "open_interest" not in data.columns:
        data["open_interest"] = np.nan
    data = data.dropna(subset=["available_at"]).sort_values("available_at")
    hourly = (
        data.set_index("available_at")
        .resample("1h", label="right", closed="right")
        .agg(funding_rate=("funding_rate", "last"), open_interest=("open_interest", "last"))
    )
    return hourly.ffill()


def _find_data_file(symbol: str, interval: str) -> str:
    matches = [
        f
        for f in list_crypto_files()
        if symbol.upper() in Path(f).name.upper() and f"_{interval}" in Path(f).name
    ]
    if not matches:
        raise FileNotFoundError(f"No {symbol} {interval} parquet found in {DATA_DIR}")
    return max(matches, key=lambda p: Path(p).stat().st_size)


def fetch_derivatives_stats(symbol: str, since: pd.Timestamp, until: pd.Timestamp) -> pd.DataFrame:
    """Fetch Binance USD-M funding and open-interest history through ccxt."""
    import ccxt

    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    market_symbol = f"{symbol[:-4]}/USDT:USDT" if symbol.upper().endswith("USDT") else symbol
    since_ms = int(since.timestamp() * 1000)
    until_ms = int(until.timestamp() * 1000)

    funding = _fetch_paginated(
        lambda start: exchange.fetch_funding_rate_history(market_symbol, since=start, limit=1000),
        since_ms,
        until_ms,
    )
    try:
        oi = _fetch_paginated(
            lambda start: exchange.fetch_open_interest_history(
                market_symbol, timeframe="1h", since=start, limit=500
            ),
            since_ms,
            until_ms,
        )
    except Exception as exc:
        print(f"warning: open-interest history unavailable, continuing funding-only: {exc}")
        oi = []

    funding_df = pd.DataFrame(
        {
            "available_at": [pd.to_datetime(x["timestamp"], unit="ms") for x in funding],
            "funding_rate": [float(x.get("fundingRate", np.nan)) for x in funding],
        }
    )
    oi_df = pd.DataFrame(
        {
            "available_at": [pd.to_datetime(x["timestamp"], unit="ms") for x in oi],
            "open_interest": [float(x.get("openInterestAmount", np.nan)) for x in oi],
        }
    )
    if funding_df.empty and oi_df.empty:
        return pd.DataFrame(columns=["available_at", "funding_rate", "open_interest"])
    if funding_df.empty:
        merged = oi_df
    elif oi_df.empty:
        merged = funding_df
    else:
        merged = pd.merge_asof(
            oi_df.sort_values("available_at"),
            funding_df.sort_values("available_at"),
            on="available_at",
            direction="backward",
            tolerance=pd.Timedelta("8h"),
        )
    return merged.sort_values("available_at").reset_index(drop=True)


def _fetch_paginated(fetch_page, since_ms: int, until_ms: int) -> list[dict]:
    rows: list[dict] = []
    cursor = since_ms
    while cursor <= until_ms:
        page = fetch_page(cursor)
        if not page:
            break
        rows.extend(
            [x for x in page if x.get("timestamp") is not None and x["timestamp"] <= until_ms]
        )
        last = max(int(x["timestamp"]) for x in page if x.get("timestamp") is not None)
        next_cursor = last + 1
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if last >= until_ms:
            break
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Build frozen market-intel from local OHLCV")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--days", type=int, default=0, help="0 means use full file")
    parser.add_argument(
        "--include-derivatives",
        action="store_true",
        help="Fetch Binance USD-M funding/open-interest history with ccxt",
    )
    parser.add_argument(
        "--derivatives-input",
        default=None,
        help="Optional frozen funding/OI CSV or Parquet with available_at columns",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_DIR / "data" / "market_intel" / "ETHUSDT_1h.parquet"),
    )
    args = parser.parse_args()

    df = load_crypto_data(_find_data_file(args.symbol, args.interval))
    df = df.sort_values("timestamp" if "timestamp" in df.columns else "datetime").reset_index(
        drop=True
    )
    if args.days > 0:
        df = df.iloc[-args.days * 288 :].reset_index(drop=True)

    derivatives = None
    if args.derivatives_input:
        path = Path(args.derivatives_input)
        derivatives = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    elif args.include_derivatives:
        datetimes = _datetimes(df)
        try:
            derivatives = fetch_derivatives_stats(args.symbol, datetimes.min(), datetimes.max())
        except Exception as exc:
            raise RuntimeError(
                "Could not fetch derivatives stats from Binance USD-M. "
                "Use --derivatives-input with a frozen CSV/Parquet instead."
            ) from exc

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    intel = build_market_intel(df, args.symbol, derivatives=derivatives)
    intel.to_parquet(out, index=False)
    print(f"Wrote {len(intel)} rows to {out}")
    print(intel["mode"].value_counts().to_string())


if __name__ == "__main__":
    main()
