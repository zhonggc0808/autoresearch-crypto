#!/usr/bin/env python3
"""Analyze Bitget signal-only loop logs and produce a 24h verification summary.

Usage:
    uv run python scripts/analyze_signal_only_log.py [--log logs/live_bitget_log.txt]

Output:
    Run duration, cycle count, errors, used_last_closed continuity,
    required_bars/cache status, regime/signal/permission distribution,
    any unexpected API calls, state anomalies, gap between cycles.
"""

import argparse
import os
import re
import sys
from datetime import datetime, timedelta


def parse_log(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    result = {
        "total_lines": len(lines),
        "first_ts": None,
        "last_ts": None,
        "cycles": 0,
        "errors": 0,
        "tracebacks": 0,
        "unclosed_timestamps": [],  # used_last_closed timestamps
        "unclosed_gaps": [],        # gaps between consecutive used_last_closed
        "required_bars_vals": [],
        "cache_bars_vals": [],
        "regime_counts": {},
        "signal_counts": {},
        "permission_reason_counts": {},
        "has_trade_api_calls": False,
        "has_state_anomalies": False,
        "lock_conflicts": 0,
        "gap_between_cycles": [],  # seconds between consecutive "开始新一轮推理"
        "cycle_timestamps": [],
        "pending_close_seen": False,
        "risk_off_close_seen": False,
    }

    for i, line in enumerate(lines):
        # Extract timestamp
        ts_match = re.match(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
        if not ts_match:
            continue
        ts_str = ts_match.group(1)
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")

        if result["first_ts"] is None:
            result["first_ts"] = ts
        result["last_ts"] = ts

        # Cycle start
        if "开始新一轮推理" in line:
            result["cycles"] += 1
            result["cycle_timestamps"].append(ts)

        # Errors / tracebacks
        if "异常" in line and "本轮执行异常" in line:
            result["errors"] += 1
        if "Traceback" in line:
            result["tracebacks"] += 1
        if "错误" in line and "本轮执行异常" not in line:
            pass  # normal error messages from retry etc.

        # used_last_closed
        if "used_last_closed=" in line:
            m = re.search(r"used_last_closed=(\d+)", line)
            if m:
                result["unclosed_timestamps"].append(int(m.group(1)))

        # required_bars
        m = re.search(r"required_bars=(\d+)", line)
        if m:
            result["required_bars_vals"].append(int(m.group(1)))

        # cache bars
        m = re.search(r"K线就绪: (\d+) bars", line)
        if m:
            result["cache_bars_vals"].append(int(m.group(1)))

        # regime
        m = re.search(r"regime=(\w+)", line)
        if m:
            r = m.group(1)
            if r in ("BULL", "BEAR", "NEUTRAL"):
                result["regime_counts"][r] = result["regime_counts"].get(r, 0) + 1

        # final_signal
        m = re.search(r"signal=(\d) \((\w+)\)", line)
        if m:
            s = f"{m.group(1)}({m.group(2)})"
            result["signal_counts"][s] = result["signal_counts"].get(s, 0) + 1

        # permission_reason
        m = re.search(r"perm=([\w\(\)]+)", line)
        if m:
            p = m.group(1)
            result["permission_reason_counts"][p] = result["permission_reason_counts"].get(p, 0) + 1

        # SIGNAL-ONLY line gives more detail
        if "[SIGNAL-ONLY]" in line:
            # Check for any trade-related API calls (shouldn't appear in signal-only)
            pass

        # PENDING_CLOSE (should NOT appear in signal-only)
        if "[PENDING_CLOSE]" in line and "created" in line.lower():
            result["pending_close_seen"] = True

        # FORCE_FLAT_CLOSE (should NOT appear in signal-only)
        if "[FORCE_FLAT_CLOSE]" in line:
            result["risk_off_close_seen"] = True

        # Trade API calls (should NOT appear in signal-only)
        trade_keywords = [
            "place_market_order", "place_limit_order", "挂单", "市价单",
            "限价单", "开多", "开空", "平多", "平空",
        ]
        if any(kw in line for kw in trade_keywords):
            if "[SIGNAL-ONLY]" not in line:
                result["has_trade_api_calls"] = True

        # File lock conflicts
        if "已有另一个" in line:
            result["lock_conflicts"] += 1

        # State anomalies
        if "异常" in line and ("pending_close" in line.lower() or "state" in line.lower()):
            result["has_state_anomalies"] = True

    # Compute consecutive gaps in used_last_closed
    timestamps_sorted = sorted(result["unclosed_timestamps"])
    for j in range(1, len(timestamps_sorted)):
        gap = (timestamps_sorted[j] - timestamps_sorted[j - 1]) / 1000  # ms → s
        result["unclosed_gaps"].append(gap)

    # Compute gaps between consecutive cycles
    for j in range(1, len(result["cycle_timestamps"])):
        gap_s = (result["cycle_timestamps"][j] - result["cycle_timestamps"][j - 1]).total_seconds()
        result["gap_between_cycles"].append(gap_s)

    return result


def print_summary(result: dict):
    duration = result["last_ts"] - result["first_ts"] if result["first_ts"] and result["last_ts"] else timedelta(0)

    print("=" * 60)
    print("  Bitget Signal-Only Loop — 24h Verification Report")
    print("=" * 60)
    print()
    print(f"  运行时长:          {duration}")
    print(f"  循环次数:          {result['cycles']}")
    print(f"  日志行数:          {result['total_lines']}")
    print()

    # Errors
    print(f"  Traceback/Error 数: {result['errors'] + result['tracebacks']}")
    print(f"    - 异常:           {result['errors']}")
    print(f"    - Traceback:      {result['tracebacks']}")
    print()

    # used_last_closed continuity
    gaps = result["unclosed_gaps"]
    if gaps:
        print(f"  used_last_closed 推进:")
        print(f"    - 记录数:        {len(result['unclosed_timestamps'])}")
        print(f"    - 平均间隔(s):   {sum(gaps)/len(gaps):.0f}")
        print(f"    - 最大间隔(s):   {max(gaps):.0f}")
        print(f"    - 最小间隔(s):   {min(gaps):.0f}")
        expected_5m = 288  # 5 minutes = 300 seconds
        large_gaps = [g for g in gaps if g > 360 * 1000]  # > 6 minutes
        if large_gaps:
            print(f"    - [!!]  超过6分钟的间隔: {len(large_gaps)} 次")
            for g in large_gaps[:5]:
                print(f"        {g/1000:.0f}s")
        else:
            print(f"    - [OK] 所有间隔 <= 6min")
    else:
        print("  used_last_closed: 无数据")
    print()

    # required_bars
    if result["required_bars_vals"]:
        print(f"  required_bars:")
        print(f"    - 最小值:        {min(result['required_bars_vals'])}")
        print(f"    - 最大值:        {max(result['required_bars_vals'])}")
        if all(v >= 74880 for v in result["required_bars_vals"]):
            print(f"    - [OK] 始终 >= 74880")
        else:
            print(f"    - [NO] 有低于 74880 的值")
    else:
        print("  required_bars: 无数据")
    print()

    # cache bars
    if result["cache_bars_vals"]:
        print(f"  cache bars:")
        print(f"    - 最小值:        {min(result['cache_bars_vals'])}")
        print(f"    - 最大值:        {max(result['cache_bars_vals'])}")
        if max(result["cache_bars_vals"]) >= 74880:
            print(f"    - [OK] 缓存充足")
        else:
            print(f"    - [!!]  缓存可能不足")
    print()

    # regime distribution
    if result["regime_counts"]:
        total_regime = sum(result["regime_counts"].values())
        print(f"  Regime 分布 (共 {total_regime} 条):")
        for r in ["BULL", "BEAR", "NEUTRAL"]:
            cnt = result["regime_counts"].get(r, 0)
            pct = cnt / total_regime * 100 if total_regime > 0 else 0
            print(f"    {r:8s}: {cnt:4d} ({pct:5.1f}%)")
    print()

    # signal distribution
    if result["signal_counts"]:
        total_sig = sum(result["signal_counts"].values())
        print(f"  Signal 分布 (共 {total_sig} 条):")
        for s, cnt in sorted(result["signal_counts"].items()):
            pct = cnt / total_sig * 100 if total_sig > 0 else 0
            print(f"    {s:15s}: {cnt:4d} ({pct:5.1f}%)")
    print()

    # permission reason distribution
    if result["permission_reason_counts"]:
        total_perm = sum(result["permission_reason_counts"].values())
        print(f"  Permission Reason 分布 (共 {total_perm} 条):")
        for p, cnt in sorted(result["permission_reason_counts"].items()):
            pct = cnt / total_perm * 100 if total_perm > 0 else 0
            print(f"    {p:30s}: {cnt:4d} ({pct:5.1f}%)")
    print()

    # Safety checks
    print(f"  [!!]  安全扫描:")
    print(f"    - 出现下单API调用:    {'[NO] YES' if result['has_trade_api_calls'] else '[OK] NO'}")
    print(f"    - PENDING_CLOSE出现:  {'[!!] YES' if result['pending_close_seen'] else '[OK] NO'}")
    print(f"    - FORCE_FLAT出现:     {'[!!] YES' if result['risk_off_close_seen'] else '[OK] NO'}")
    print(f"    - 文件锁冲突:          {result['lock_conflicts']}")
    print(f"    - State 异常:          {'[!!] YES' if result['has_state_anomalies'] else '[OK] NO'}")

    if result["gap_between_cycles"]:
        max_cycle_gap = max(result["gap_between_cycles"])
        print(f"    - 最长两轮间隔(s):    {max_cycle_gap:.0f}")
        if max_cycle_gap > 600:
            print(f"      [!!]  超过10分钟，可能有断档")

    print()
    print("=" * 60)

    # Overall verdict
    print()
    verdict = "PASS" if (
        result["errors"] == 0
        and result["tracebacks"] == 0
        and not result["has_trade_api_calls"]
        and not result["pending_close_seen"]
        and not result["risk_off_close_seen"]
        and all(v >= 74880 for v in result.get("required_bars_vals", [74880]))
    ) else "REVIEW"
    print(f"  总体评价: {verdict}")
    if verdict == "PASS":
        print("  信号管道正常，可以进入 demo --once 阶段。")
    else:
        print("  需检查上述 [!!] 项目后再决定是否进入 demo --once。")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Analyze Bitget signal-only loop log")
    parser.add_argument("--log", default="logs/live_bitget_log.txt", help="Path to log file")
    args = parser.parse_args()

    if not os.path.exists(args.log):
        print(f"错误: 未找到日志文件 {args.log}")
        sys.exit(1)

    result = parse_log(args.log)
    print_summary(result)


if __name__ == "__main__":
    main()
