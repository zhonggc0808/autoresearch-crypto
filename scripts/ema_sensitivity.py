#!/usr/bin/env python3
"""EMA regime sensitivity sweep for exp_0015 (BEAR-only).
Tests EMA 20/100, 50/200, 100/300 on 1300d and 2600d.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib.util
spec = importlib.util.spec_from_file_location("orc", str(Path(__file__).resolve().parents[1] / "scripts" / "research_oracle.py"))
orc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orc)

from dex.config import DATA_DIR

candidates = {
    "EMA 20/100": "exp_0015_ema20_100",
    "EMA 50/200": "exp_0015",
    "EMA 100/300": "exp_0015_ema100_300",
}
data_configs = {"1300d": None, "2600d": None}

# Find 2600d data path
data_dir = Path(DATA_DIR)
eth_2600 = list(data_dir.glob("ETHUSDT*2600d*"))
if eth_2600:
    data_configs["2600d"] = eth_2600[0]

print(f"{'EMA':<14} {'Data':<8} {'IS Ret':<10} {'IS DD':<10} {'OOS Ret':<10} {'OOS DD':<10} {'Sharpe':<10} {'Trades':<8} {'DD50':<6} {'DD40':<6} {'FEE':<6} {'RN':<6} {'Roll6m':<10} {'Roll12m':<10}")
print("-" * 130)

import types
_orig_find = orc._find_eth_data

for ema_label, cand_name in candidates.items():
    for data_label, data_path in data_configs.items():
        if data_path is not None:
            def make_finder(p):
                return lambda: p
            orc._find_eth_data = make_finder(data_path)
        else:
            orc._find_eth_data = _orig_find

        try:
            cand_path = str(Path(__file__).resolve().parents[1] / "research_workspace" / "candidates" / f"{cand_name}.json")
            r = orc.run_oracle(candidate_path=cand_path)
            m = r["metrics"]
            is_m = m["is"]["raw"]
            oos_m = m["oos"]["raw"]
            f = r["flags"]
            roll = m["rolling"]

            dd50 = "Y" if "DD_OVER_50" in f.get("disqualifications", []) or "DD_OVER_50" in f.get("baseline_known_risks", []) else "N"
            dd40 = "Y" if "DD_OVER_40" in f.get("warnings", []) else "N"
            fee = "Y" if "FEE_FRAGILE" in f.get("warnings", []) else "N"
            rn = "Y" if "ROLLING_NEGATIVE" in f.get("disqualifications", []) else "N"

            print(f"{ema_label:<14} {data_label:<8} {is_m['return']*100:+7.2f}% {is_m['dd']*100:>7.2f}% {oos_m['return']*100:+7.2f}% {oos_m['dd']*100:>7.2f}% {oos_m['sharpe']:<8.4f} {oos_m['trades']:<6} {dd50:<6} {dd40:<6} {fee:<6} {rn:<6} {roll['6m_min_return']*100:+7.2f}% {roll['12m_min_return']*100:+7.2f}%")
        except Exception as e:
            print(f"{ema_label:<14} {data_label:<8} ERROR: {e}")
