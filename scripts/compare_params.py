#!/usr/bin/env python3
"""Compare v2.1 baseline params vs exp_0012 side by side."""
import json
from pathlib import Path

b = json.loads(Path("research_workspace/baselines/channel_breakout_v2_1_balanced_params.json").read_text())
e = json.loads(Path("research_workspace/candidates/exp_0012.json").read_text())["params"]

print(f"{'Parameter':<35} {'v2.1 baseline':<25} {'exp_0012':<25}")
print("-" * 85)

print(f"{'regime_change_policy':<35} {b['regime_change_policy']:<25} {e['regime_change_policy']:<25}")
print(f"{'regime_filter':<35} {str(b['regime_filter']):<25} {str(e['regime_filter']):<25}")
print()

for regime in ["bull", "bear", "neutral"]:
    bp = b[regime]["strategy_params"]
    ep = e[regime]["strategy_params"]
    print(f"--- {regime.upper()} ---")
    for key in bp:
        bv = bp[key]
        ev = ep.get(key, "N/A")
        marker = " <<<" if str(bv) != str(ev) else ""
        print(f"  {key:<33} {str(bv):<25} {str(ev):<25}{marker}")

    print(f"  permission:")
    bperm = b[regime]["permission"]
    eperm = e[regime]["permission"]
    for key in bperm:
        bv = bperm[key]
        ev = eperm.get(key, "N/A")
        marker = " <<<" if str(bv) != str(ev) else ""
        print(f"    {key:<31} {str(bv):<25} {str(ev):<25}{marker}")
    for key in eperm:
        if key not in bperm:
            print(f"    {key:<31} {'N/A':<25} {str(eperm[key]):<25} <<< NEW")
    print()

print(f"--- FILTER (exp_0012 only) ---")
filt = json.loads(Path("research_workspace/candidates/exp_0012.json").read_text()).get("filter")
if filt:
    print(f"  type: {filt['type']}")
    print(f"  adx_threshold: {filt['adx_threshold']}")
    print(f"  apply_to: {filt['apply_to']}")
else:
    print("  (none)")
