#!/usr/bin/env python3
"""Run oracle for baseline and exp_0012, print side-by-side comparison."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
with open(str(Path(__file__).resolve().parents[1] / "scripts" / "research_oracle.py"), encoding="utf-8") as f:
    exec(f.read().split('if __name__')[0])

b = run_oracle(use_baseline=True)
e = run_oracle(candidate_path=str(PROJECT_DIR / "research_workspace" / "candidates" / "exp_0012.json"))

def fmt(v, pct=False):
    if v is None: return "N/A"
    if pct: return f"{v*100:+7.2f}%"
    return f"{v:.4f}"

print(f"\n{'':<25} {'v2.1 baseline':<18} {'exp_0012':<18} {'Delta':<10}")
print("-" * 75)

# IS
bi = b["metrics"]["is"]["raw"]
ei = e["metrics"]["is"]["raw"]
print(f"{'IS Return':<25} {fmt(bi['return'],1):<18} {fmt(ei['return'],1):<18} {fmt(ei['return']-bi['return'],1):<10}")
print(f"{'IS DD':<25} {fmt(bi['dd'],1):<18} {fmt(ei['dd'],1):<18} {fmt(ei['dd']-bi['dd'],1):<10}")
print(f"{'IS Sharpe':<25} {fmt(bi['sharpe']):<18} {fmt(ei['sharpe']):<18} {fmt(ei['sharpe']-bi['sharpe']):<10}")
print(f"{'IS Trades':<25} {bi['trades']:<18} {ei['trades']:<18} {ei['trades']-bi['trades']:<+10}")
print(f"{'IS Win Rate':<25} {fmt(bi['win_rate'],1):<18} {fmt(ei['win_rate'],1):<18}")
print()

# OOS raw
bo = b["metrics"]["oos"]["raw"]
eo = e["metrics"]["oos"]["raw"]
print(f"{'OOS Return':<25} {fmt(bo['return'],1):<18} {fmt(eo['return'],1):<18} {fmt(eo['return']-bo['return'],1):<10}")
print(f"{'OOS DD':<25} {fmt(bo['dd'],1):<18} {fmt(eo['dd'],1):<18} {fmt(eo['dd']-bo['dd'],1):<10}")
print(f"{'OOS Sharpe':<25} {fmt(bo['sharpe']):<18} {fmt(eo['sharpe']):<18} {fmt(eo['sharpe']-bo['sharpe']):<10}")
print(f"{'OOS Trades':<25} {bo['trades']:<18} {eo['trades']:<18} {eo['trades']-bo['trades']:<+10}")
print(f"{'OOS Win Rate':<25} {fmt(bo['win_rate'],1):<18} {fmt(eo['win_rate'],1):<18}")
print(f"{'OOS Trades/yr':<25} {bo['trades_per_year']:<18} {eo['trades_per_year']:<18}")
print()

# OOS safe
bs = b["metrics"]["oos"]["safe_execution"]
es_ = e["metrics"]["oos"]["safe_execution"]
print(f"{'Safe Return':<25} {fmt(bs['return'],1):<18} {fmt(es_['return'],1):<18} {fmt(es_['return']-bs['return'],1):<10}")
print(f"{'Safe DD':<25} {fmt(bs['dd'],1):<18} {fmt(es_['dd'],1):<18}")
print(f"{'Exec Parity':<25} {fmt(b['metrics']['execution_parity']):<18} {fmt(e['metrics']['execution_parity']):<18}")
print()

# Rolling
print(f"{'Roll 6m Min':<25} {fmt(b['metrics']['rolling']['6m_min_return'],1):<18} {fmt(e['metrics']['rolling']['6m_min_return'],1):<18}")
print(f"{'Roll 12m Min':<25} {fmt(b['metrics']['rolling']['12m_min_return'],1):<18} {fmt(e['metrics']['rolling']['12m_min_return'],1):<18}")
print()

# Flags
bf = b["flags"]
ef_ = e["flags"]
print(f"{'Status':<25} {bf['status']:<18} {ef_['status']:<18}")
print(f"{'Disqualifications':<25} {','.join(bf.get('disqualifications',[])):<18} {','.join(ef_.get('disqualifications',[])):<18}")
print(f"{'Known Risks':<25} {','.join(bf.get('baseline_known_risks',[])):<18} {'N/A':<18}")
print()

# Sensitivity
bsens = b["metrics"]["sensitivity"]["oos"]["fees"]
esens = e["metrics"]["sensitivity"]["oos"]["fees"]
print(f"{'Fee 0bp OOS':<25} {fmt(bsens['0bp'],1):<18} {fmt(esens['0bp'],1):<18}")
print(f"{'Fee 10bp OOS':<25} {fmt(bsens['10bp'],1):<18} {fmt(esens['10bp'],1):<18}")

# Correlation
corr = e["metrics"].get("correlation", {}).get("vs_baseline", "N/A")
print(f"{'Corr vs Baseline':<25} {'1.0':<18} {fmt(corr):<18}")
