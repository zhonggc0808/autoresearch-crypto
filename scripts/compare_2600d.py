#!/usr/bin/env python3
"""Compare baseline vs exp_0012 on 2600-day data."""
from __future__ import annotations
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Import oracle module
import importlib.util
spec = importlib.util.spec_from_file_location("orc", str(Path(__file__).resolve().parents[1] / "scripts" / "research_oracle.py"))
orc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orc)

# Override data finder to force 2600d
from dex.config import DATA_DIR
data_dir = Path(DATA_DIR)
eth_2600 = list(data_dir.glob("ETHUSDT*2600d*"))
if not eth_2600:
    print("ERROR: ETHUSDT 2600d not found. Available:")
    for f in sorted(data_dir.glob("ETHUSDT*")):
        print(f"  {f.name}")
    sys.exit(1)

data_path = eth_2600[0]
print(f"Data: {data_path.name}")

# Override _find_eth_data to return 2600d
def _find_2600():
    return data_path
orc._find_eth_data = _find_2600

# Run baseline and candidate
b = orc.run_oracle(use_baseline=True)
e = orc.run_oracle(candidate_path=str(Path(__file__).resolve().parents[1] / "research_workspace" / "candidates" / "exp_0012.json"))

def fmt(v, pct=False):
    if v is None: return "N/A"
    if pct: return f"{v*100:+7.2f}%"
    return f"{v:.4f}"

print(f"\n{'':<28} {'v2.1 baseline':<20} {'exp_0012':<20} {'Delta':<10}")
print("-" * 80)

for section, label in [("is", "IS"), ("oos", "OOS")]:
    bi = b["metrics"][section]["raw"]
    ei = e["metrics"][section]["raw"]
    print(f"\n  --- {label} (2600d) ---")
    print(f"{'Return':<28} {fmt(bi['return'],1):<20} {fmt(ei['return'],1):<20} {fmt(ei['return']-bi['return'],1):<10}")
    print(f"{'DD':<28} {fmt(bi['dd'],1):<20} {fmt(ei['dd'],1):<20} {fmt(ei['dd']-bi['dd'],1):<10}")
    print(f"{'Sharpe':<28} {fmt(bi['sharpe']):<20} {fmt(ei['sharpe']):<20} {fmt(ei['sharpe']-bi['sharpe']):<10}")
    print(f"{'Trades':<28} {bi['trades']:<20} {ei['trades']:<20} {ei['trades']-bi['trades']:<+10}")
    print(f"{'Win Rate':<28} {fmt(bi['win_rate'],1):<20} {fmt(ei['win_rate'],1):<20}")

# Rolling
print(f"\n  --- Rolling (2600d) ---")
print(f"{'6m Min Return':<28} {fmt(b['metrics']['rolling']['6m_min_return'],1):<20} {fmt(e['metrics']['rolling']['6m_min_return'],1):<20}")
print(f"{'12m Min Return':<28} {fmt(b['metrics']['rolling']['12m_min_return'],1):<20} {fmt(e['metrics']['rolling']['12m_min_return'],1):<20}")

# Sensitivity
print(f"\n  --- Sensitivity (2600d) ---")
print(f"{'Fee 10bp OOS':<28} {fmt(b['metrics']['sensitivity']['oos']['fees']['10bp'],1):<20} {fmt(e['metrics']['sensitivity']['oos']['fees']['10bp'],1):<20}")
print(f"{'Exec Parity':<28} {fmt(b['metrics']['execution_parity']):<20} {fmt(e['metrics']['execution_parity']):<20}")
print(f"{'Corr vs Baseline':<28} {'1.0':<20} {fmt(e['metrics']['correlation']['vs_baseline']):<20}")

# Flags
print(f"\n  --- Flags (2600d) ---")
print(f"{'Status':<28} {b['flags']['status']:<20} {e['flags']['status']:<20}")
print(f"{'Disqualifications':<28} {','.join(b['flags'].get('disqualifications',[])):<20} {','.join(e['flags'].get('disqualifications',[])):<20}")
if b['flags'].get('baseline_known_risks'):
    print(f"{'Known Risks':<28} {','.join(b['flags']['baseline_known_risks']):<20}")
