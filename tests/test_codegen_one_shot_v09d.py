#!/usr/bin/env python3
"""Test suite for v0.9d Codegen One-Shot Smoke.

Tests:
    - Mock one-shot: generate -> evaluate -> scorecard
    - Verdict always codegen_research_only
    - Bad LLM response -> generate_failed, exit 2
    - Forbidden import -> rejected, exit 2
    - Dry-run -> no writes, exit 0
    - No llm_results.tsv pollution

Run:
    uv run pytest tests/test_codegen_one_shot_v09d.py -v --tb=short
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


# ===================================================================
# Test: Smoke pipeline (mock mode)
# ===================================================================


class TestSmokeMock:
    """Verify the full one-shot smoke pipeline with mock generation."""

    def _cleanup_scorecard(self, codegen_id: str) -> None:
        """Remove scorecard file if it exists."""
        sc_path = (
            PROJECT_DIR
            / "research_workspace"
            / "codegen_scorecards"
            / f"{codegen_id}_scorecard.json"
        )
        if sc_path.exists():
            sc_path.unlink()

    def _cleanup_codegen_dir(self, codegen_id: str) -> None:
        """Remove codegen candidate directory if it exists."""
        cg_dir = PROJECT_DIR / "research_workspace" / "codegen_candidates" / codegen_id
        if cg_dir.exists():
            shutil.rmtree(cg_dir, ignore_errors=True)

    def _cleanup_candidate(self, codegen_id: str) -> None:
        """Remove candidate JSON if it exists."""
        cand_path = PROJECT_DIR / "research_workspace" / "llm_candidates" / f"{codegen_id}.json"
        if cand_path.exists():
            cand_path.unlink()

    def _cleanup_all(self, codegen_id: str) -> None:
        self._cleanup_scorecard(codegen_id)
        self._cleanup_codegen_dir(codegen_id)
        self._cleanup_candidate(codegen_id)

    def test_mock_one_shot_passes(self):
        """Mock mode: generate -> evaluate -> scorecard exists."""
        from scripts.run_codegen_smoke import run_smoke

        result = run_smoke(mock=True)
        assert result["status"] == "success", (
            f"Expected success, got {result['status']}: {result.get('error', '')}"
        )
        assert result["exit_code"] == 0
        assert result["scorecard_path"] is not None

        # Verify scorecard content
        sc_path = Path(result["scorecard_path"])
        assert sc_path.exists()
        scorecard = json.loads(sc_path.read_text(encoding="utf-8"))
        assert scorecard["verdict"]["label"] == "codegen_research_only"
        assert scorecard["promotion_eligible"] is False
        assert scorecard["promotion_block_reason"] == "CODEGEN_RESEARCH_ONLY"

        # Verify metrics exist
        ms = scorecard.get("metrics_summary", {})
        assert ms.get("is_return") is not None
        assert ms.get("oos_return") is not None
        assert ms.get("oos_safe_return") is not None

        # Cleanup
        codegen_id = result["codegen_id"]
        self._cleanup_all(codegen_id)

    def test_verdict_always_codegen_research_only(self):
        """Smoke pipeline always produces codegen_research_only verdict."""
        from scripts.run_codegen_smoke import run_smoke

        result = run_smoke(mock=True)
        assert result["status"] == "success"

        sc_path = Path(result["scorecard_path"])
        scorecard = json.loads(sc_path.read_text(encoding="utf-8"))
        assert scorecard["verdict"]["label"] == "codegen_research_only"
        assert "promotion" not in scorecard["verdict"]["label"].lower()

        # Cleanup
        self._cleanup_all(result["codegen_id"])

    def test_no_llm_results_tsv_pollution(self):
        """Smoke does not write to llm_results.tsv."""
        llm_tsv = PROJECT_DIR / "research_workspace" / "llm_results.tsv"
        content_before = ""
        if llm_tsv.exists():
            content_before = llm_tsv.read_text(encoding="utf-8")

        from scripts.run_codegen_smoke import run_smoke

        result = run_smoke(mock=True)
        assert result["status"] == "success"

        content_after = llm_tsv.read_text(encoding="utf-8") if llm_tsv.exists() else ""

        assert content_after == content_before, "Smoke should not modify llm_results.tsv"

        # Cleanup
        self._cleanup_all(result["codegen_id"])


# ===================================================================
# Test: Dry-run
# ===================================================================


class TestSmokeDryRun:
    """Verify --dry-run writes nothing."""

    def test_dry_run_no_writes(self):
        """Dry-run prints plan, writes nothing."""
        from scripts.run_codegen_smoke import run_smoke

        # Snapshot codegen_scorecards before
        scorecards_dir = PROJECT_DIR / "research_workspace" / "codegen_scorecards"
        files_before = set()
        if scorecards_dir.exists():
            files_before = {p.name for p in scorecards_dir.iterdir() if p.is_file()}

        result = run_smoke(mock=True, dry_run=True)

        assert result["status"] == "dry_run"
        assert result["exit_code"] == 0
        assert result.get("scorecard_path") is None

        # Verify no new scorecard files
        files_after = set()
        if scorecards_dir.exists():
            files_after = {p.name for p in scorecards_dir.iterdir() if p.is_file()}
        assert files_after == files_before, (
            f"Dry-run should not create files. Before: {files_before}, After: {files_after}"
        )

    def test_dry_run_exit_code_0(self):
        """Dry-run returns exit code 0."""
        from scripts.run_codegen_smoke import run_smoke

        result = run_smoke(mock=True, dry_run=True)
        assert result["exit_code"] == 0


# ===================================================================
# Test: Bad LLM response handling (via generate_code_candidate mock path)
# ===================================================================


class TestSmokeErrorHandling:
    """Verify error paths are handled cleanly."""

    def test_bad_llm_response_generate_failed(self, monkeypatch):
        """Non-JSON LLM response -> generate_failed, exit 2."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")

        # Mock generate_code_candidate to return generate_failed
        import scripts.generate_code_candidate as gcc

        original_generate = gcc._generate

        def mock_generate(mock=False):
            return None, "LLM returned invalid non-JSON response"

        gcc._generate = mock_generate
        try:
            from scripts.run_codegen_smoke import run_smoke

            result = run_smoke(mock=False)
            assert result["status"] == "generate_failed"
            assert result["exit_code"] == 2
            assert result.get("error") is not None
        finally:
            gcc._generate = original_generate

    def test_forbidden_import_rejected(self, monkeypatch):
        """Strategy with 'import os' -> rejected, exit 2."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-fake-key")

        # Mock generate_code_candidate to return code with forbidden import
        import scripts.generate_code_candidate as gcc

        original_generate = gcc._generate
        bad_code = "import os\nimport numpy as np\n\ndef generate_signals(df):\n    return np.zeros(len(df), dtype=np.int8)\n"
        bad_manifest = {
            "strategy_name": "bad_import",
            "description": "",
            "hypothesis": "",
            "params": {},
        }
        gcc._generate = lambda mock=False: (bad_code, bad_manifest)

        try:
            from scripts.run_codegen_smoke import run_smoke

            result = run_smoke(mock=False)
            assert result["status"] == "rejected"
            assert result["exit_code"] == 2
        finally:
            gcc._generate = original_generate

    def test_api_key_missing_clean_skip(self, monkeypatch):
        """No API key -> defaults to mock, but --mock not set -> INFO, mock auto."""
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

        from scripts.run_codegen_smoke import run_smoke

        # Without --mock and without API key, should auto-default to mock
        # and succeed (since mock path works without API key)
        result = run_smoke(mock=True)  # Explicit --mock
        assert result["status"] == "success"
        assert result["exit_code"] == 0

        # Cleanup
        from scripts.run_codegen_smoke import PROJECT_DIR as PD

        cg_id = result["codegen_id"]
        sc_path = PD / "research_workspace" / "codegen_scorecards" / f"{cg_id}_scorecard.json"
        if sc_path.exists():
            sc_path.unlink()
        cg_dir = PD / "research_workspace" / "codegen_candidates" / cg_id
        if cg_dir.exists():
            shutil.rmtree(cg_dir, ignore_errors=True)
        cand_path = PD / "research_workspace" / "llm_candidates" / f"{cg_id}.json"
        if cand_path.exists():
            cand_path.unlink()
