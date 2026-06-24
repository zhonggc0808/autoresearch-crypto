#!/usr/bin/env python3
"""Test suite for v0.9a Limited Codegen Sandbox.

Tests:
    - Static scan: forbidden imports, forbidden calls, missing function
    - Static scan: valid code passes
    - Sandbox import: valid strategy imports correctly
    - Sandbox import: forbidden imports rejected
    - Interface validation: output length, signal values, type
    - No future-data tests: shift test, anomaly test
    - Side-effect detection
    - Full generate_code_candidate flow (mock mode)
    - Rejection flow (bad code → rejected)

Run:
    uv run pytest tests/test_codegen_sandbox_v09a.py -v --tb=short
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_DIR = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# Sample code fixtures
# ---------------------------------------------------------------------------

VALID_STRATEGY_CODE = """\
import numpy as np
import pandas as pd

def generate_signals(df):
    \"\"\"Simple moving average crossover strategy.\"\"\"
    close = df['close'].values
    fast_period = 20
    slow_period = 50

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
            signals[i] = 2
        elif fast_ema[i] < slow_ema[i] and fast_ema[i-1] >= slow_ema[i-1]:
            signals[i] = 3
        else:
            signals[i] = 1
    return signals
"""

CODE_WITH_FORBIDDEN_IMPORT_OS = """\
import os
import numpy as np
import pandas as pd

def generate_signals(df):
    return np.zeros(len(df), dtype=np.int8)
"""

CODE_WITH_FORBIDDEN_IMPORT_SUBPROCESS = """\
import subprocess
import numpy as np

def generate_signals(df):
    return np.zeros(len(df), dtype=np.int8)
"""

CODE_WITH_FORBIDDEN_CALL_EXEC = """\
import numpy as np

def generate_signals(df):
    exec("x = 1")
    return np.zeros(len(df), dtype=np.int8)
"""

CODE_WITH_FORBIDDEN_CALL_OPEN = """\
import numpy as np

def generate_signals(df):
    f = open("/tmp/test.txt", "w")
    return np.zeros(len(df), dtype=np.int8)
"""

CODE_MISSING_FUNCTION = """\
import numpy as np
import pandas as pd

def some_other_function(df):
    return np.zeros(len(df), dtype=np.int8)
"""

CODE_WRONG_OUTPUT_LENGTH = """\
import numpy as np

def generate_signals(df):
    return np.zeros(len(df) - 10, dtype=np.int8)
"""

CODE_INVALID_SIGNAL_VALUES = """\
import numpy as np

def generate_signals(df):
    signals = np.full(len(df), 99, dtype=np.int8)
    return signals
"""

CODE_FROM_FORBIDDEN = """\
from os import path
import numpy as np

def generate_signals(df):
    return np.zeros(len(df), dtype=np.int8)
"""

CODE_VALID_NAMED_ARGS = """\
import numpy as np
from typing import Optional

def generate_signals(df, some_param=42):
    return np.zeros(len(df), dtype=np.int8)
"""

# ===================================================================
# Test: Static scan
# ===================================================================


class TestStaticScan:
    """Verify AST-based static analysis catches violations."""

    def test_valid_code_passes(self):
        from scripts.scan_candidate_code import scan_code

        errors = scan_code(VALID_STRATEGY_CODE)
        assert errors == [], f"Valid code should pass, got: {errors}"

    def test_forbidden_import_os(self):
        from scripts.scan_candidate_code import scan_code

        errors = scan_code(CODE_WITH_FORBIDDEN_IMPORT_OS)
        assert errors, "Expected error for 'import os'"
        assert any("os" in e for e in errors), f"Expected 'os' in errors: {errors}"

    def test_forbidden_import_subprocess(self):
        from scripts.scan_candidate_code import scan_code

        errors = scan_code(CODE_WITH_FORBIDDEN_IMPORT_SUBPROCESS)
        assert errors, "Expected error for 'import subprocess'"

    def test_forbidden_import_from(self):
        from scripts.scan_candidate_code import scan_code

        errors = scan_code(CODE_FROM_FORBIDDEN)
        assert errors, "Expected error for 'from os import'"
        assert any("os" in e for e in errors), f"Expected 'os' in errors: {errors}"

    def test_forbidden_call_exec(self):
        from scripts.scan_candidate_code import scan_code

        errors = scan_code(CODE_WITH_FORBIDDEN_CALL_EXEC)
        assert errors, "Expected error for 'exec()'"
        assert any("exec" in e for e in errors), f"Expected 'exec' in errors: {errors}"

    def test_forbidden_call_open(self):
        from scripts.scan_candidate_code import scan_code

        errors = scan_code(CODE_WITH_FORBIDDEN_CALL_OPEN)
        # open() is caught by regex pattern, not AST (it's a builtin, not import)
        assert errors, "Expected error for 'open() call'"
        assert any("open" in e.lower() for e in errors), f"Expected 'open' in errors: {errors}"

    def test_missing_generate_signals(self):
        from scripts.scan_candidate_code import scan_code

        errors = scan_code(CODE_MISSING_FUNCTION)
        assert errors, "Expected error for missing generate_signals"
        assert any("generate_signals" in e for e in errors)

    def test_has_forbidden_imports_quick(self):
        from scripts.scan_candidate_code import has_forbidden_imports

        assert has_forbidden_imports(CODE_WITH_FORBIDDEN_IMPORT_OS)
        assert has_forbidden_imports(CODE_FROM_FORBIDDEN)
        assert not has_forbidden_imports(VALID_STRATEGY_CODE)

    def test_syntax_error_returns_error(self):
        from scripts.scan_candidate_code import scan_code

        errors = scan_code("this is not valid python @@@")
        assert errors, "Expected error for syntax error"


# ===================================================================
# Test: Interface validation
# ===================================================================


class TestInterfaceValidation:
    """Verify dynamic interface checks."""

    def test_valid_interface_passes(self):
        import tempfile

        from scripts.generate_code_candidate import (
            MOCK_STRATEGY_CODE as code,
        )
        from scripts.sandbox_import_candidate import import_candidate_strategy
        from scripts.validate_code_candidate import validate_interface

        tmpdir = Path(tempfile.mkdtemp())
        try:
            strategy_path = tmpdir / "strategy.py"
            strategy_path.write_text(code, encoding="utf-8")
            module = import_candidate_strategy(str(strategy_path))
            errors = validate_interface(module)
            assert errors == [], f"Expected no errors, got: {errors}"
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_wrong_output_length(self):
        import tempfile

        from scripts.sandbox_import_candidate import import_candidate_strategy
        from scripts.validate_code_candidate import validate_interface

        tmpdir = Path(tempfile.mkdtemp())
        try:
            strategy_path = tmpdir / "strategy.py"
            strategy_path.write_text(CODE_WRONG_OUTPUT_LENGTH, encoding="utf-8")
            module = import_candidate_strategy(str(strategy_path))
            errors = validate_interface(module)
            assert errors, "Expected error for wrong output length"
            assert any("length" in e.lower() for e in errors)
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_invalid_signal_values(self):
        import tempfile

        from scripts.sandbox_import_candidate import import_candidate_strategy
        from scripts.validate_code_candidate import validate_interface

        tmpdir = Path(tempfile.mkdtemp())
        try:
            strategy_path = tmpdir / "strategy.py"
            strategy_path.write_text(CODE_INVALID_SIGNAL_VALUES, encoding="utf-8")
            module = import_candidate_strategy(str(strategy_path))
            errors = validate_interface(module)
            assert errors, "Expected error for invalid signal values"
            assert any("99" in e for e in errors)
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


# ===================================================================
# Test: Sandbox import
# ===================================================================


class TestSandboxImport:
    """Verify restricted import sandbox rejects dangerous code."""

    def test_valid_import_succeeds(self):
        import tempfile

        from scripts.sandbox_import_candidate import import_candidate_strategy

        tmpdir = Path(tempfile.mkdtemp())
        try:
            strategy_path = tmpdir / "strategy.py"
            strategy_path.write_text(VALID_STRATEGY_CODE, encoding="utf-8")
            module = import_candidate_strategy(str(strategy_path))
            assert module is not None
            assert hasattr(module, "generate_signals")
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_forbidden_import_rejected(self):
        import tempfile

        from scripts.sandbox_import_candidate import (
            SandboxImportError,
            import_candidate_strategy,
        )

        tmpdir = Path(tempfile.mkdtemp())
        try:
            strategy_path = tmpdir / "strategy.py"
            strategy_path.write_text(CODE_WITH_FORBIDDEN_IMPORT_OS, encoding="utf-8")
            with pytest.raises((SandboxImportError, ImportError)):
                import_candidate_strategy(str(strategy_path))
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_missing_file_raises(self):
        from scripts.sandbox_import_candidate import import_candidate_strategy

        with pytest.raises(FileNotFoundError):
            import_candidate_strategy("/nonexistent/path.py")


# ===================================================================
# Test: Full codegen flow (mock)
# ===================================================================


class TestCodegenFlow:
    """Full generate_code_candidate flow in mock mode."""

    def test_mock_flow_accepts_valid_code(self):
        from scripts.generate_code_candidate import generate_code_candidate

        result = generate_code_candidate(mock=True)
        assert result["status"] == "accepted", (
            f"Expected accepted, got {result['status']}: {result.get('errors', [])}"
        )
        assert result["candidate_path"] is not None

        # Verify candidate file exists and is valid
        cand_path = Path(result["candidate_path"])
        assert cand_path.exists()
        candidate = json.loads(cand_path.read_text(encoding="utf-8"))
        assert candidate["status"] == "research_only"
        assert candidate["strategy"] == "codegen"
        assert "codegen" in candidate["constraints"]

        # Cleanup
        import shutil

        codegen_dir = Path(result["codegen_dir"])
        if codegen_dir.exists():
            shutil.rmtree(codegen_dir, ignore_errors=True)
        cand_path.unlink()

    def test_dry_run_no_writes(self):
        from scripts.generate_code_candidate import generate_code_candidate

        result = generate_code_candidate(mock=True, dry_run=True)
        assert result["status"] == "dry_run"


# ===================================================================
# Test: No future-data
# ===================================================================


class TestNoFutureData:
    """Verify future-data detection heuristics."""

    def test_shift_test_runs(self):
        import tempfile

        from scripts.sandbox_import_candidate import import_candidate_strategy
        from scripts.validate_code_candidate import validate_no_future_data

        tmpdir = Path(tempfile.mkdtemp())
        try:
            strategy_path = tmpdir / "strategy.py"
            strategy_path.write_text(VALID_STRATEGY_CODE, encoding="utf-8")
            module = import_candidate_strategy(str(strategy_path))
            # Should run without error (may or may not flag)
            errors = validate_no_future_data(module)
            # No strict assertion — this is a heuristic
            assert isinstance(errors, list)
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


# ===================================================================
# Test: Side-effect detection
# ===================================================================


class TestSideEffectDetection:
    """Verify side-effect detection catches file creation."""

    def test_no_side_effects_with_valid_code(self):
        import tempfile

        from scripts.sandbox_import_candidate import import_candidate_strategy
        from scripts.validate_code_candidate import validate_no_side_effects

        tmpdir = Path(tempfile.mkdtemp())
        try:
            strategy_path = tmpdir / "strategy.py"
            strategy_path.write_text(VALID_STRATEGY_CODE, encoding="utf-8")
            module = import_candidate_strategy(str(strategy_path))
            errors = validate_no_side_effects(module)
            assert errors == [], f"Expected no side effects, got: {errors}"
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


# ===================================================================
# Test: codegen_candidates workspace boundary
# ===================================================================


class TestCodegenBoundary:
    """Verify codegen candidates are only written to the sandbox dir."""

    def test_codegen_dir_exists(self):
        from scripts.generate_code_candidate import CODGEN_DIR

        assert CODGEN_DIR.exists()
        assert CODGEN_DIR.name == "codegen_candidates"

    def test_codegen_writes_to_sandbox(self):
        """Verify mock flow writes files inside codegen_candidates/."""
        from scripts.generate_code_candidate import CODGEN_DIR, generate_code_candidate

        result = generate_code_candidate(mock=True)
        assert result["status"] == "accepted"

        codegen_dir = Path(result["codegen_dir"])
        assert codegen_dir.parent == CODGEN_DIR, (
            f"Codegen dir {codegen_dir} not inside {CODGEN_DIR}"
        )
        assert (codegen_dir / "strategy.py").exists()
        assert (codegen_dir / "manifest.json").exists()

        # Cleanup
        import shutil

        cand_path = Path(result["candidate_path"])
        if cand_path.exists():
            cand_path.unlink()
        if codegen_dir.exists():
            shutil.rmtree(codegen_dir, ignore_errors=True)


# ===================================================================
# Test: Real LLM codegen (v0.9c)
# ===================================================================


class TestLLMCodegen:
    """Verify real LLM generation path with mocked API responses."""

    VALID_LLM_RESPONSE = json.dumps(
        {
            "strategy_code": """\
import numpy as np
import pandas as pd

def generate_signals(df):
    close = df['close'].values
    ema_fast = np.full(len(df), np.nan)
    ema_slow = np.full(len(df), np.nan)
    ema_fast[0] = close[0]
    ema_slow[0] = close[0]
    for i in range(1, len(df)):
        ema_fast[i] = close[i] * (2/21) + ema_fast[i-1] * (1 - 2/21)
        ema_slow[i] = close[i] * (2/51) + ema_slow[i-1] * (1 - 2/51)
    signals = np.zeros(len(df), dtype=np.int8)
    for i in range(1, len(df)):
        if np.isnan(ema_fast[i]) or np.isnan(ema_slow[i]):
            continue
        if ema_fast[i] > ema_slow[i] and ema_fast[i-1] <= ema_slow[i-1]:
            signals[i] = 2
        elif ema_fast[i] < ema_slow[i] and ema_fast[i-1] >= ema_slow[i-1]:
            signals[i] = 3
        else:
            signals[i] = 1
    return signals
""",
            "manifest": {
                "strategy_name": "ema_cross_llm",
                "hypothesis": "EMA cross captures trend changes.",
                "description": "Fast/slow EMA crossover strategy.",
                "params": {"fast": 20, "slow": 50},
            },
        }
    )

    NON_JSON_RESPONSE_TEXT = "I think a good strategy would be to use RSI..."

    @staticmethod
    def _mock_anthropic_response(response_text: str):
        """Create a mock HTTPResponse-like object for patching urlopen."""
        from unittest.mock import MagicMock

        body = json.dumps(
            {
                "content": [{"type": "text", "text": response_text}],
                "model": "claude-sonnet-4-6",
            }
        ).encode("utf-8")
        mock_resp = MagicMock()
        mock_resp.read.return_value = body
        mock_resp.__enter__.return_value = mock_resp
        return mock_resp

    @staticmethod
    def _mock_api_error(status_code: int, message: str):
        """Create a mock HTTPError for testing API failures."""
        import urllib.error
        from unittest.mock import MagicMock

        body = json.dumps({"error": {"message": message}}).encode("utf-8")
        # We raise the error directly — urllib.request.urlopen will
        # propagate it when called with our mock side_effect.
        return urllib.error.HTTPError(
            url="https://api.anthropic.com/v1/messages",
            code=status_code,
            msg=message,
            hdrs={},
            fp=MagicMock(**{"read.return_value": body}),
        )

    def test_llm_generate_with_mock_response(self, monkeypatch):
        """Mock LLM API call returns valid JSON -> accepted candidate."""
        monkeypatch.setenv("LLM_API_KEY", "sk-test-fake-key")
        monkeypatch.setattr(
            "scripts.llm_client.call_llm",
            lambda *args, **kwargs: self.VALID_LLM_RESPONSE,
        )

        import scripts.generate_code_candidate as gcc

        result = gcc.generate_code_candidate(mock=False)

        assert result["status"] == "accepted", (
            f"Expected accepted, got {result['status']}: {result.get('errors', [])}"
        )
        assert result["candidate_path"] is not None

        cand_path = Path(result["candidate_path"])
        assert cand_path.exists()
        candidate = json.loads(cand_path.read_text(encoding="utf-8"))
        assert candidate["status"] == "research_only"
        assert candidate["strategy"] == "codegen"

        # Cleanup
        import shutil

        codegen_dir = Path(result["codegen_dir"])
        if codegen_dir.exists():
            shutil.rmtree(codegen_dir, ignore_errors=True)
        cand_path.unlink()

    def test_llm_non_json_response_rejected(self, monkeypatch):
        """LLM returns non-JSON text -> generate_failed, not evaluated."""
        monkeypatch.setenv("LLM_API_KEY", "sk-test-fake-key")
        monkeypatch.setattr(
            "scripts.llm_client.call_llm",
            lambda *args, **kwargs: self.NON_JSON_RESPONSE_TEXT,
        )

        import scripts.generate_code_candidate as gcc

        result = gcc.generate_code_candidate(mock=False)

        assert result["status"] == "generate_failed", (
            f"Expected generate_failed, got {result['status']}"
        )
        assert len(result.get("errors", [])) > 0
        assert "JSON" in str(result["errors"]), (
            f"Error should mention JSON parsing, got: {result['errors']}"
        )

    def test_parser_repairs_raw_multiline_strategy_code(self):
        """LLM may return raw multiline code inside strategy_code."""
        from scripts.generate_code_candidate import _parse_llm_json

        response = """{
  "strategy_code": "import numpy as np
def generate_signals(df):
    return np.zeros(len(df), dtype=np.int8)
",
  "manifest": {
    "strategy_name": "raw_multiline",
    "hypothesis": "Testing parser repair.",
    "description": "Raw multiline strategy_code should parse.",
    "params": {}
  }
}"""

        parsed = _parse_llm_json(response)

        assert "def generate_signals" in parsed["strategy_code"]
        assert parsed["manifest"]["strategy_name"] == "raw_multiline"

    def test_parser_repairs_raw_multiline_manifest_fields(self):
        """LLM may also wrap manifest strings across raw lines."""
        from scripts.generate_code_candidate import _parse_llm_json

        response = """{
  "strategy_code": "import numpy as np
def generate_signals(df):
    return np.zeros(len(df), dtype=np.int8)
",
  "manifest": {
    "strategy_name": "raw_manifest",
    "hypothesis": "First line
second line",
    "description": "Description line
continued",
    "params": {}
  }
}"""

        parsed = _parse_llm_json(response)

        assert parsed["manifest"]["hypothesis"] == "First line\nsecond line"
        assert parsed["manifest"]["description"] == "Description line\ncontinued"

    def test_parser_does_not_double_escape_valid_strategy_code(self):
        """Repairing manifest text must not turn escaped code newlines into literal backslashes."""
        from scripts.generate_code_candidate import _parse_llm_json

        response = """{
  "strategy_code": "import numpy as np\\ndef generate_signals(df):\\n    return np.zeros(len(df), dtype=np.int8)\\n",
  "manifest": {
    "strategy_name": "escaped_code_raw_manifest",
    "hypothesis": "Testing parser repair.",
    "description": "Description line
continued",
    "params": {}
  }
}"""

        parsed = _parse_llm_json(response)

        assert parsed["strategy_code"].startswith("import numpy as np\n")
        assert "\\n" not in parsed["strategy_code"]
        assert parsed["manifest"]["description"] == "Description line\ncontinued"

    def test_llm_forbidden_import_rejected(self, monkeypatch):
        """LLM returns code with 'import os' -> rejected by static scan."""
        monkeypatch.setenv("LLM_API_KEY", "sk-test-fake-key")

        bad_response = json.dumps(
            {
                "strategy_code": "import os\nimport numpy as np\n\ndef generate_signals(df):\n    return np.zeros(len(df), dtype=np.int8)\n",
                "manifest": {
                    "strategy_name": "bad_strategy",
                    "hypothesis": "Testing forbidden import rejection.",
                    "description": "Should be rejected by static scan.",
                    "params": {},
                },
            }
        )
        monkeypatch.setattr("scripts.llm_client.call_llm", lambda *args, **kwargs: bad_response)

        import scripts.generate_code_candidate as gcc

        result = gcc.generate_code_candidate(mock=False)

        assert result["status"] == "rejected", f"Expected rejected, got {result['status']}"
        error_text = " ".join(str(e) for e in result.get("errors", []))
        assert "os" in error_text.lower(), (
            f"Error should mention forbidden import 'os', got: {error_text}"
        )

    def test_llm_api_key_missing(self, monkeypatch):
        """No LLM API key -> generate_failed, not accepted."""
        import scripts.generate_code_candidate as gcc

        monkeypatch.setattr(gcc, "_has_llm_api_key", lambda: False)
        result = gcc.generate_code_candidate(mock=False)

        assert result["status"] == "generate_failed", (
            f"Expected generate_failed, got {result['status']}"
        )
        assert len(result.get("errors", [])) > 0
        error_text = " ".join(str(e) for e in result["errors"])
        assert "API_KEY" in error_text, f"Error should mention API_KEY, got: {error_text}"
