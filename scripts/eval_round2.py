#!/usr/bin/env python3
"""Evaluate round-2 candidates and print comparison table."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Direct import - module sets up sys.path on its own
exec(open(str(Path(__file__).resolve().parents[1] / "scripts" / "research_oracle.py")).read().split("if __name__")[0])

baseline = o.run_oracle(use_baseline=True)
bm = baseline["metrics"]["oos"]["raw"]

rows = []
for i in range(5, 9):
    p = o.PROJECT_DIR / "research_workspace" / "candidates" / f"exp_000{i}.json"
    if not p.exists():
        continue
    r = o.run_oracle(candidate_path=str(p))
    m = r["metrics"]
    oos = m["oos"]["raw"]
    is_ = m["is"]["raw"]
    f = r["flags"]
    corr = m.get("correlation", {}).get("vs_baseline", 0)
    fee10 = m["sensitivity"]["oos"]["fees"]["10bp"]
    rows.append({
        "id": f"exp_000{i}",
        "oos_r": oos["return"], "oos_dd": oos["dd"], "oos_sh": oos["sharpe"],
        "oos_tr": oos["trades"], "is_r": is_["return"], "is_dd": is_["dd"],
        "corr": corr, "fee10": fee10, "status": f["status"],
        "disc": "|".join(f.get("disqualifications", [])),
        "warn": "|".join(f.get("warnings", [])),
    })

# Baseline + rounded 1 top
print(f"{'Candidate':<12} {'OOS Return':<18} {'OOS DD':<12} {'OOS Sharpe':<14} {'Trades':<8} {'IS Return':<18} {'Corr':<8} {'Fee10bp':<12} {'Status':<10} {'Flags'}")
print("="*150)
print(f"{'baseline':<12} {bm['return']*100:+7.1f}% (---)  {bm['dd']*100:+7.1f}%  {bm['sharpe']:<8.4f}  {bm['trades']:<10} {'':>5} {bm['return']*100:+7.1f}%   {1.0:<8} {0:<12} {'BASELINE':<10} (DD_OVER_40|CORR_BASELINE_099)")

for r in rows:
    ret_d = r["oos_r"] - bm["return"]
    dd_d = r["oos_dd"] - bm["dd"]
    flags = r["status"]
    if r["disc"]: flags += f" [D:{r['disc']}]"
    if r["warn"]: flags += f" (W:{r['warn']})"
    print(f"{r['id']:<12} {r['oos_r']*100:+7.1f}% ({ret_d*100:+5.1f}%)  {r['oos_dd']*100:+7.1f}%  {r['oos_sh']:<8.4f}  {r['oos_tr']:<10} {'':>5} {r['is_r']*100:+7.1f}%  {r['corr']:<8.4f} {r['fee10']*100:+7.1f}%  {r['status']:<10} {flags}")
