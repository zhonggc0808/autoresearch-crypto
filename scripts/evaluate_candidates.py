#!/usr/bin/env python3
"""Evaluate baseline + all candidates, print comparison table."""
from __future__ import annotations
import sys, json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib.util
spec = importlib.util.spec_from_file_location("oracle", Path(__file__).resolve().parents[1] / "scripts" / "research_oracle.py")
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)

baseline = oracle.run_oracle(use_baseline=True)
candidates = []
for i in range(1, 5):
    p = oracle.PROJECT_DIR / "research_workspace" / "candidates" / f"exp_000{i}.json"
    if p.exists():
        candidates.append(oracle.run_oracle(candidate_path=str(p)))

def fmt_pct(v):
    return f"{v*100:+.1f}%"

def short(v, d=4):
    return f"{v:.{d}f}" if v is not None else "N/A"

# Header
print(f"{'Candidate':<18} {'OOS Return':<12} {'OOS DD':<10} {'OOS Sharpe':<12} {'OOS Trades':<12} {'Trades/yr':<10} {'IS Return':<12} {'IS DD':<10} {'Corr v21':<10} {'Fee10bp OOS':<12} {'Flags'}")
print("-" * 140)

# Baseline
bm = baseline["metrics"]
br = bm["oos"]["raw"]
bi = bm["is"]["raw"]
f = baseline["flags"]
corr = bm.get("correlation", {}).get("vs_baseline", 1.0)
fee10 = bm["sensitivity"]["oos"]["fees"]["10bp"]
status = f["status"]
disc = "|".join(f["disqualifications"]) if f.get("disqualifications") else ""
warn = "|".join(f["warnings"]) if f.get("warnings") else ""
flags_str = f"{status}"
if disc: flags_str += f" [{disc}]"
if warn: flags_str += f" ({warn})"

print(f"{'baseline (v2.1)':<18} {fmt_pct(br['return']):<12} {fmt_pct(br['dd']):<10} {short(br['sharpe']):<12} {br['trades']:<12} {short(br['trades_per_year'],1):<10} {fmt_pct(bi['return']):<12} {fmt_pct(bi['dd']):<10} {short(corr):<10} {fmt_pct(fee10):<12} {flags_str}")

for c in candidates:
    m = c["metrics"]
    r = m["oos"]["raw"]
    i = m["is"]["raw"]
    f = c["flags"]
    corr = m.get("correlation", {}).get("vs_baseline", "N/A")
    fee10 = m["sensitivity"]["oos"]["fees"]["10bp"]
    status = f["status"]
    disc = "|".join(f["disqualifications"]) if f.get("disqualifications") else ""
    warn = "|".join(f["warnings"]) if f.get("warnings") else ""
    flags_str = f"{status}"
    if disc: flags_str += f" [{disc}]"
    if warn: flags_str += f" ({warn})"
    print(f"{c['experiment_id']:<18} {fmt_pct(r['return']):<12} {fmt_pct(r['dd']):<10} {short(r['sharpe']):<12} {r['trades']:<12} {short(r['trades_per_year'],1):<10} {fmt_pct(i['return']):<12} {fmt_pct(i['dd']):<10} {short(corr):<10} {fmt_pct(fee10):<12} {flags_str}")

print()
print("=== Summary ===")
for c in candidates:
    eid = c["experiment_id"]
    r = c["metrics"]["oos"]["raw"]
    i = c["metrics"]["is"]["raw"]
    f = c["flags"]
    corr = c["metrics"].get("correlation", {}).get("vs_baseline", 0)
    fee10 = c["metrics"]["sensitivity"]["oos"]["fees"]["10bp"]
    ret_delta = r["return"] - baseline["metrics"]["oos"]["raw"]["return"]
    dd_delta = r["dd"] - baseline["metrics"]["oos"]["raw"]["dd"]
    print(f"{eid}: OOS return {fmt_pct(r['return'])} (delta {fmt_pct(ret_delta)}), "
          f"DD {fmt_pct(r['dd'])} (delta {fmt_pct(dd_delta)}), "
          f"Sharpe {short(r['sharpe'])}, corr {short(corr)}, "
          f"status {f['status']}, fee10bp {fmt_pct(fee10)}")
    if f.get("disqualifications"):
        print(f"  !! Disqualified: {f['disqualifications']}")
    if f.get("warnings"):
        print(f"  !! Warnings: {f['warnings']}")
