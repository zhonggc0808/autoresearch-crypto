#!/usr/bin/env python3
"""Code Candidate Generator v0.9a — LLM generates strategy code → sandbox → validate.

Flow:
    1. LLM generates strategy.py + manifest.json (or mock for testing)
    2. Static scan (forbidden imports/calls)
    3. Import sandbox (restricted env)
    4. Interface validation (generate_signals, output constraints)
    5. Future-data tests (shift, anomaly)
    6. Side-effect detection
    7. If all pass: wrap as research-only candidate
    8. If any fail: write rejection record

Usage:
    # Mock mode (no LLM API key)
    uv run python scripts/generate_code_candidate.py --mock

    # Full mode
    uv run python scripts/generate_code_candidate.py

    # Dry run (show context, no writes)
    uv run python scripts/generate_code_candidate.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

CODGEN_DIR = PROJECT_DIR / "research_workspace" / "codegen_candidates"
LLM_CANDIDATES_DIR = PROJECT_DIR / "research_workspace" / "llm_candidates"
REJECTED_DIR = PROJECT_DIR / "research_workspace" / "proposals" / "rejected_candidates"
CODGEN_PROMPT_PATH = PROJECT_DIR / "research_agents" / "prompts" / "codegen_candidate.md"


# ---------------------------------------------------------------------------
# Mock responses
# ---------------------------------------------------------------------------

MOCK_STRATEGY_CODE = """\
import numpy as np
import pandas as pd

def generate_signals(df):
    \"\"\"EMA cross strategy: go long when fast EMA > slow EMA.\"\"\"
    close = df['close'].values
    fast_period = 20
    slow_period = 50

    # Compute EMAs
    fast_ema = np.full(len(df), np.nan)
    slow_ema = np.full(len(df), np.nan)

    fast_ema[0] = close[0]
    slow_ema[0] = close[0]

    for i in range(1, len(df)):
        k_fast = 2.0 / (fast_period + 1)
        k_slow = 2.0 / (slow_period + 1)
        fast_ema[i] = close[i] * k_fast + fast_ema[i-1] * (1 - k_fast)
        slow_ema[i] = close[i] * k_slow + slow_ema[i-1] * (1 - k_slow)

    signals = np.zeros(len(df), dtype=np.int8)

    for i in range(1, len(df)):
        if np.isnan(fast_ema[i]) or np.isnan(slow_ema[i]):
            continue
        if fast_ema[i] > slow_ema[i] and fast_ema[i-1] <= slow_ema[i-1]:
            signals[i] = 2  # long
        elif fast_ema[i] < slow_ema[i] and fast_ema[i-1] >= slow_ema[i-1]:
            signals[i] = 3  # short
        else:
            signals[i] = 1  # hold

    return signals
"""

MOCK_MANIFEST = {
    "strategy_name": "ema_cross_v1",
    "description": "EMA cross strategy: fast EMA crosses above slow EMA → long, crosses below → short",
    "hypothesis": "EMA cross on 5m data captures trend changes earlier than Donchian breakout, increasing trade frequency while maintaining win rate.",
    "expected_behavior_change": "Trade frequency increases 2-3x vs Donchian baseline. Win rate may drop but risk-adjusted return improves.",
    "params": {
        "fast_period": 20,
        "slow_period": 50,
    },
}


# ---------------------------------------------------------------------------
# ID generation
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _next_codegen_id() -> str:
    """Generate next codegen candidate ID."""
    import re

    CODGEN_DIR.mkdir(parents=True, exist_ok=True)
    existing = [d.name for d in CODGEN_DIR.iterdir() if d.is_dir() and d.name.startswith("exp_")]
    if LLM_CANDIDATES_DIR.exists():
        existing.extend(f.stem for f in LLM_CANDIDATES_DIR.glob("exp_*.json"))
    if REJECTED_DIR.exists():
        for f in REJECTED_DIR.glob("*exp_*.json"):
            match = re.search(r"(exp_\d+)", f.stem)
            if match:
                existing.append(match.group(1))
    nums = []
    for name in existing:
        try:
            nums.append(int(name.split("_")[1]))
        except (IndexError, ValueError):
            pass
    next_num = max(nums) + 1 if nums else 1
    return f"exp_{next_num:04d}"


# ---------------------------------------------------------------------------
# Core flow
# ---------------------------------------------------------------------------


def generate_code_candidate(
    *,
    dry_run: bool = False,
    mock: bool = False,
) -> Dict[str, Any]:
    """Generate, scan, sandbox, and validate a code candidate.

    Returns a result dict with status and details.
    """
    cg_id = _next_codegen_id()
    cg_dir = CODGEN_DIR / cg_id
    strategy_path = cg_dir / "strategy.py"
    manifest_path = cg_dir / "manifest.json"

    result: Dict[str, Any] = {
        "codegen_id": cg_id,
        "status": "error",
        "errors": [],
        "warnings": [],
        "candidate_path": None,
    }

    print(f"\n{'=' * 60}")
    print("  Codegen Candidate v0.9a")
    print(f"  ID: {cg_id}")
    print(f"  Mode: {'MOCK' if mock else 'LIVE'}")
    print(f"{'=' * 60}")

    # --- Step 1: Generate code + manifest ---
    print("\n[Step 1] Generating strategy code ...")
    strategy_code, manifest = _generate(mock=mock)
    if strategy_code is None:
        error_msg = manifest if isinstance(manifest, str) else "Failed to generate strategy code"
        result["status"] = "generate_failed"
        result["errors"] = [error_msg]
        print(f"  [FAIL] {error_msg}")
        return result
    print(f"  Code length: {len(strategy_code)} chars")
    strat_name = manifest.get("strategy_name") or manifest.get("name") or "?"
    print(f"  Strategy: {strat_name}")

    if dry_run:
        print("\n  (dry-run: stopping here, no files written)")
        result["status"] = "dry_run"
        return result

    # --- Step 2: Static scan ---
    print("\n[Step 2] Static scan ...")
    from scripts.scan_candidate_code import scan_code

    scan_errors = scan_code(strategy_code)
    if scan_errors:
        print(f"  [FAIL] {len(scan_errors)} violation(s):")
        for e in scan_errors:
            print(f"    - {e}")
        _write_rejection(cg_id, "static_scan_failed", scan_errors, strategy_code, manifest)
        result["status"] = "rejected"
        result["errors"] = scan_errors
        return result
    print("  [OK] No violations")

    # --- Step 3: Write codegen files ---
    print("\n[Step 3] Writing codegen files ...")
    cg_dir.mkdir(parents=True, exist_ok=True)
    strategy_path.write_text(strategy_code, encoding="utf-8")
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    print(f"  Strategy: {strategy_path}")
    print(f"  Manifest: {manifest_path}")

    # --- Step 4: Sandbox import ---
    print("\n[Step 4] Sandbox import ...")
    from scripts.sandbox_import_candidate import (
        SandboxImportError,
        import_candidate_strategy,
    )

    try:
        module = import_candidate_strategy(str(strategy_path))
    except SandboxImportError as e:
        print(f"  [FAIL] Sandbox violation: {e}")
        _write_rejection(cg_id, "sandbox_violation", [str(e)], strategy_code, manifest)
        result["status"] = "rejected"
        result["errors"] = [str(e)]
        return result
    except Exception as e:
        print(f"  [FAIL] Import failed: {e}")
        _write_rejection(cg_id, "import_failed", [str(e)], strategy_code, manifest)
        result["status"] = "rejected"
        result["errors"] = [str(e)]
        return result
    print(f"  [OK] Module imported: {module.__name__}")

    # --- Step 5: Interface validation ---
    print("\n[Step 5] Interface validation ...")
    from scripts.validate_code_candidate import validate_candidate_code

    validation_errors = validate_candidate_code(module)
    if validation_errors:
        print(f"  [FAIL] {len(validation_errors)} error(s):")
        for e in validation_errors:
            print(f"    - {e}")
        _write_rejection(cg_id, "validation_failed", validation_errors, strategy_code, manifest)
        result["status"] = "rejected"
        result["errors"] = validation_errors
        return result
    print("  [OK] All checks passed")

    # --- Step 6: Wrap as research candidate ---
    print("\n[Step 6] Wrapping as research candidate ...")
    candidate = _wrap_as_candidate(cg_id, manifest, strategy_path)
    cand_path = LLM_CANDIDATES_DIR / f"{cg_id}.json"
    cand_path.write_text(json.dumps(candidate, indent=2, default=str), encoding="utf-8")
    print(f"  Candidate: {cand_path}")
    print("  Status: research_only")

    result["status"] = "accepted"
    result["candidate_path"] = str(cand_path)
    result["codegen_dir"] = str(cg_dir)
    result["warnings"] = []

    print(f"\n{'=' * 60}")
    print(f"  Codegen candidate accepted: {cg_id}")
    print(f"  Path: {cg_dir}")
    print(f"{'=' * 60}\n")

    return result


# ---------------------------------------------------------------------------
# Sub-steps
# ---------------------------------------------------------------------------


def _load_prompt_text() -> str:
    """Load the LLM prompt from the codegen prompt file."""
    if not CODGEN_PROMPT_PATH.exists():
        raise FileNotFoundError(f"Prompt file not found: {CODGEN_PROMPT_PATH}")
    return CODGEN_PROMPT_PATH.read_text(encoding="utf-8")


def _generate_via_llm() -> tuple:
    """Call the configured LLM API to generate strategy code and manifest.

    Returns (strategy_code, manifest) on success.
    Returns (None, error_message) on any failure.

    The LLM prompt contains only the function interface, allowed imports,
    signal encoding rules, and output JSON schema. No project internals
    are exposed.
    """
    import os

    if not _has_llm_api_key():
        return None, (
            "LLM_API_KEY or ANTHROPIC_API_KEY environment variable not set. "
            "Use --mock for testing without an API key."
        )

    from scripts.llm_client import call_llm

    model = os.environ.get("CODEGEN_MODEL", "claude-sonnet-4-6")
    prompt_text = _load_prompt_text()

    response_text = ""
    for attempt in range(3):
        try:
            response_text = call_llm(
                system_prompt=(
                    "You are a strategy code generator. Respond with valid JSON only, "
                    "no markdown fences, no explanations."
                ),
                user_message=prompt_text,
                model=model,
                max_tokens=8192,
                temperature=0.7 if attempt == 0 else 0.2,
            )
        except Exception as e:
            return None, f"LLM call failed: {e}"
        if response_text:
            break

    if not response_text:
        return None, "LLM returned empty response"

    # Parse JSON from response (strip markdown fences if present)
    try:
        parsed = _parse_llm_json(response_text)
    except ValueError as e:
        return None, str(e)

    strategy_code = parsed.get("strategy_code", "").strip()
    manifest = parsed.get("manifest", {})

    if not strategy_code:
        return None, "LLM response missing 'strategy_code' field"
    if not isinstance(manifest, dict):
        return None, "LLM response 'manifest' is not a JSON object"

    # Normalize manifest: LLM uses "name", downstream code expects "strategy_name"
    if "name" in manifest and "strategy_name" not in manifest:
        manifest["strategy_name"] = manifest.pop("name")

    return strategy_code, manifest


def _has_llm_api_key() -> bool:
    """Return whether a configured LLM API key is available."""
    import os

    import scripts.llm_client  # noqa: F401 - loads .env for key detection

    return bool(os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))


def _extract_api_error(http_error: Any) -> str:
    """Extract human-readable error detail from an API HTTP error."""
    try:
        import json

        body = json.loads(http_error.read().decode("utf-8"))
        return body.get("error", {}).get("message", str(body))
    except Exception:
        return str(http_error)


def _parse_llm_json(response_text: str) -> dict:
    """Parse JSON from LLM response, stripping markdown fences if present.

    Accepts:
        {"strategy_code": "..."}           — bare JSON
        ```json\n{"strategy_code": "..."}  — fenced JSON
        ```\n{"strategy_code": "..."}      — fenced without language tag
    """
    import json
    import re

    text = response_text.strip()

    # Strip markdown code fences if present
    fence_pattern = re.compile(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)
    match = fence_pattern.match(text)
    if match:
        text = match.group(1).strip()

    # Try parsing
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        repaired = _repair_common_raw_json_strings(text)
        if repaired is not None:
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
        raise ValueError(f"LLM response is not valid JSON: {e}\nResponse preview: {text[:300]}")


def _repair_common_raw_json_strings(text: str) -> str | None:
    """Repair common raw multiline strings in codegen JSON output."""
    repaired = _repair_raw_strategy_code_json(text) or text
    for field, followers in (
        ("hypothesis", ("description", "expected_behavior_change", "params")),
        ("description", ("expected_behavior_change", "params")),
        ("expected_behavior_change", ("params",)),
    ):
        repaired = _repair_raw_string_field_before_keys(repaired, field, followers) or repaired
    return repaired if repaired != text else None


def _repair_raw_strategy_code_json(text: str) -> str | None:
    """Repair the common case where only strategy_code is a raw multiline string."""
    import json
    import re

    start_match = re.search(r'("strategy_code"\s*:\s*")', text)
    if not start_match:
        return None
    code_start = start_match.end()
    tail_match = re.search(r'"\s*,\s*"manifest"\s*:', text[code_start:], re.DOTALL)
    if not tail_match:
        return None

    code_end = code_start + tail_match.start()
    raw_code = text[code_start:code_end]
    if "\n" not in raw_code:
        return None
    escaped_code = json.dumps(raw_code)[1:-1]
    return text[:code_start] + escaped_code + text[code_end:]


def _repair_raw_string_field_before_keys(
    text: str,
    field: str,
    followers: tuple[str, ...],
) -> str | None:
    """Repair a raw multiline string field that appears before known next keys."""
    import json
    import re

    start_match = re.search(rf'("{re.escape(field)}"\s*:\s*")', text)
    if not start_match:
        return None
    value_start = start_match.end()
    follower_pattern = "|".join(re.escape(key) for key in followers)
    tail_match = re.search(
        rf'"\s*,\s*"({follower_pattern})"\s*:',
        text[value_start:],
        re.DOTALL,
    )
    if not tail_match:
        return None

    value_end = value_start + tail_match.start()
    raw_value = text[value_start:value_end]
    escaped_value = json.dumps(raw_value)[1:-1]
    return text[:value_start] + escaped_value + text[value_end:]


def _generate(
    mock: bool = False,
) -> tuple:
    """Generate strategy code and manifest (mock or LLM).

    For real generation, calls the Anthropic API with the prompt from
    CODGEN_PROMPT_PATH. The LLM receives ONLY the interface definition
    and allowed libraries — no project internals (oracle, baseline,
    scorecard, file system structure).

    Returns (strategy_code, manifest) on success, or (None, error_msg)
    on failure.

    Parameters
    ----------
    mock : bool
        If True, return hardcoded mock code/manifest (no API call).
        If False, attempt real LLM generation via Anthropic API.
    """
    if mock:
        return MOCK_STRATEGY_CODE, dict(MOCK_MANIFEST)

    return _generate_via_llm()


def _wrap_as_candidate(
    cg_id: str,
    manifest: Dict[str, Any],
    strategy_path: Path,
) -> Dict[str, Any]:
    """Wrap a validated codegen candidate into a research candidate spec."""
    import hashlib

    code_hash = hashlib.sha256(strategy_path.read_bytes()).hexdigest()[:16]

    return {
        "experiment_id": cg_id,
        "parent_id": "codegen_baseline",
        "candidate_role": "standalone",
        "strategy": "codegen",
        "base": "codegen_v0.9a",
        "status": "research_only",
        "hypothesis": manifest.get(
            "hypothesis",
            "Code-generated strategy with custom signal logic.",
        ),
        "constraints": [
            "no_future_data",
            "no_demo_routing",
            "research_only",
            "codegen",
        ],
        "expected_behavior_change": manifest.get(
            "expected_behavior_change",
            "Code-generated strategy with validated interface.",
        ),
        "params": {
            "strategy_type": "codegen_strategy",
            "code_path": str(strategy_path.resolve()),
            "code_hash": code_hash,
            "codegen_params": manifest.get("params", {}),
        },
    }


def _write_rejection(
    cg_id: str,
    reason: str,
    errors: List[str],
    code: str,
    manifest: Dict[str, Any],
) -> None:
    """Write a rejection record for failed codegen candidate."""
    REJECTED_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "codegen_id": cg_id,
        "timestamp": _now_iso(),
        "rejection_reason": reason,
        "errors": errors,
        "strategy_name": manifest.get("strategy_name", "?"),
    }
    path = REJECTED_DIR / f"rejected_codegen_{cg_id}.json"
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    print(f"  Rejection record: {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _resolve_mock_default(cli_mock: bool) -> bool:
    """Resolve mock mode: CLI flag wins, else auto-detect from API key."""
    if cli_mock:
        return True
    if _has_llm_api_key():
        return False
    print("  [INFO] No LLM_API_KEY or ANTHROPIC_API_KEY set. Defaulting to --mock.")
    print("  [INFO] Set LLM_API_KEY (or CODEGEN_MODEL) for real LLM generation.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Codegen Candidate Generator v0.9a")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use mock code/manifest (no LLM API key needed)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show context, skip writes",
    )
    args = parser.parse_args()

    result = generate_code_candidate(
        dry_run=args.dry_run,
        mock=_resolve_mock_default(args.mock),
    )

    if result["status"] == "accepted":
        print(f"\n[OK] Codegen candidate accepted: {result['codegen_id']}")
        sys.exit(0)
    elif result["status"] == "dry_run":
        print("\n[OK] Dry-run complete.")
        sys.exit(0)
    else:
        print(f"\n[FAIL] {result.get('status', 'error')}: {result.get('errors', [])}")
        sys.exit(1)


if __name__ == "__main__":
    main()
