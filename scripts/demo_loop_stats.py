#!/usr/bin/env python3
"""Lightweight live demo loop stats — fills, maker rate, cancellations, delays.

Usage:
    uv run python scripts/demo_loop_stats.py

Output:
    Fills today, maker fill rate, cancel rate, avg fill delay, pending_open timeouts.
"""
import os, re, sys
from datetime import datetime, timedelta

LOG_FILE = "logs/live_bitget_log.txt"
if not os.path.exists(LOG_FILE):
    print(f"Log not found: {LOG_FILE}")
    sys.exit(1)

with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
    lines = f.readlines()

# Only count today's entries
today = datetime.now().strftime("%Y-%m-%d")
today_lines = [l for l in lines if l.startswith(f"[{today}")]

stats = {
    "total_cycles": 0,
    "fills": 0,           # PENDING_OPEN_FILLED or Maker成交
    "maker_orders": 0,    # limit orders placed
    "cancels": 0,         # explicit order cancellations
    "timeouts": 0,        # pending_open still open after check
    "ioc_fallbacks": 0,   # maker→taker fallback
    "errors": 0,          # 异常 lines
    "force_closes": 0,    # force_close calls
    "pending_open_filled": 0,
    "pending_open_canceled": 0,
    "pending_close_filled": 0,
    "skip_cycles": 0,     # 无需换仓 or skip
}

for line in today_lines:
    if "开始新一轮推理" in line:
        stats["total_cycles"] += 1
    if "PENDING_OPEN_FILLED" in line:
        stats["fills"] += 1
        stats["pending_open_filled"] += 1
    if "PENDING_OPEN_CANCELED" in line:
        stats["pending_open_canceled"] += 1
    if "PENDING_CLOSE_FILLED" in line:
        stats["pending_close_filled"] += 1
    if "限价单成功" in line:
        stats["maker_orders"] += 1
    if "取消所有挂单" in line:
        stats["cancels"] += 1
    if "市价兜底" in line or "切换市价单" in line:
        stats["ioc_fallbacks"] += 1
    if "本轮执行异常" in line:
        stats["errors"] += 1
    if "强制平仓" in line:
        stats["force_closes"] += 1
    if "无需换仓" in line:
        stats["skip_cycles"] += 1

# Maker fill rate estimate
maker_fill_rate = (stats["fills"] / stats["maker_orders"] * 100) if stats["maker_orders"] > 0 else 0
cancel_rate = (stats["cancels"] / stats["maker_orders"] * 100) if stats["maker_orders"] > 0 else 0

print("=" * 50)
print(f"  Demo Loop Stats — {today}")
print("=" * 50)
print(f"  Total cycles:     {stats['total_cycles']}")
print(f"  Skip (hold):      {stats['skip_cycles']}")
print(f"  Errors:           {stats['errors']}")
print()
print(f"  Maker orders:     {stats['maker_orders']}")
print(f"  Fills:            {stats['fills']}")
print(f"    - PENDING_OPEN_FILLED:   {stats['pending_open_filled']}")
print(f"    - PENDING_CLOSE_FILLED:  {stats['pending_close_filled']}")
print(f"    - PENDING_OPEN_CANCELED: {stats['pending_open_canceled']}")
print(f"  Maker fill rate:  {maker_fill_rate:.0f}%")
print(f"  Cancel rate:      {cancel_rate:.0f}%")
print(f"  IOC fallbacks:    {stats['ioc_fallbacks']}")
print(f"  Force closes:     {stats['force_closes']}")
print()
if stats['errors'] == 0:
    print("  STATUS: CLEAN")
else:
    print(f"  STATUS: {stats['errors']} errors — check logs")
print("=" * 50)
