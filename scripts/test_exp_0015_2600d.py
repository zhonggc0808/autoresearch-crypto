#!/usr/bin/env python3
"""Test exp_0015 (BEAR-only) on 2600d data."""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import importlib.util
spec = importlib.util.spec_from_file_location("orc", str(Path(__file__).resolve().parents[1] / "scripts" / "research_oracle.py"))
orc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orc)

from dex.config import DATA_DIR
data_dir = Path(DATA_DIR)
eth = list(data_dir.glob("ETHUSDT*2600d*"))
if not eth:
    print("2600d data not found")
    sys.exit(1)
orc._find_eth_data = lambda: eth[0]

r = orc.run_oracle(candidate_path=str(Path(__file__).resolve().parents[1] / "research_workspace" / "candidates" / "exp_0015.json"))
m = r["metrics"]
o = m["oos"]["raw"]
i = m["is"]["raw"]
f = r["flags"]

print(f"2600d IS:  Return={i['return']*100:+7.2f}%  DD={i['dd']*100:.2f}%  Sharpe={i['sharpe']:.4f}  Trades={i['trades']}")
print(f"2600d OOS: Return={o['return']*100:+7.2f}%  DD={o['dd']*100:.2f}%  Sharpe={o['sharpe']:.4f}  Trades={o['trades']}")
print(f"Status: {f['status']}  Disq={'|'.join(f.get('disqualifications',[]))}  Warn={'|'.join(f.get('warnings',[]))}")
print(f"Roll 6m: {m['rolling']['6m_min_return']*100:+.2f}%  12m: {m['rolling']['12m_min_return']*100:+.2f}%")
print(f"Fee 10bp OOS: {m['sensitivity']['oos']['fees']['10bp']*100:+.2f}%")
print(f"Exec parity: {m['execution_parity']:.4f}")
