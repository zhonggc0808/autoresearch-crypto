#!/usr/bin/env python3
"""Static code scanner for LLM-generated strategies (v0.9a).

Performs AST-based analysis to detect:
    - Forbidden imports (os, subprocess, socket, etc.)
    - Forbidden function calls (exec, eval, open, compile, __import__)
    - Dangerous patterns and anti-patterns

Usage:
    from scripts.scan_candidate_code import scan_code, FORBIDDEN_IMPORTS

    errors = scan_code(source_code_string)
    if errors:
        print("Code rejected:", errors)
"""

from __future__ import annotations

import ast
import re
from typing import List, Tuple

# ---------------------------------------------------------------------------
# Forbidden lists
# ---------------------------------------------------------------------------

FORBIDDEN_IMPORTS: set = {
    # System / process
    "os", "subprocess", "sys", "signal", "mmap", "ctypes",
    # Network
    "socket", "requests", "urllib", "http", "httplib", "ssl",
    # File / persistence
    "pathlib", "shutil", "pickle", "shelve", "sqlite3", "dbm",
    # Code execution
    "importlib", "code", "codeop", "pty", "compileall", "py_compile",
    # Reflection (dangerous in sandbox)
    "inspect",
    # Multiprocessing / threading (forbidden in codegen context)
    "multiprocessing", "threading", "concurrent",
    # Crypto (not needed for strategy)
    "cryptography", "hashlib",
}

FORBIDDEN_CALLS: set = {
    "exec", "eval", "compile", "__import__", "breakpoint", "input",
}

FORBIDDEN_CALL_PATTERNS: List[Tuple[str, str]] = [
    (r"open\s*\(", "File open() call"),
    (r"getattr\s*\(.*__", "Reflective attribute access via getattr(__)"),
]

# Allowed top-level names in the module
REQUIRED_FUNCTION = "generate_signals"
ALLOWED_SIGNAL_VALUES = {0, 1, 2, 3}


# ---------------------------------------------------------------------------
# Static analysis
# ---------------------------------------------------------------------------


def scan_code(source: str, filename: str = "<codegen>") -> List[str]:
    """Run static analysis on candidate strategy source code.

    Returns a list of error messages (empty = no violations found).
    """
    errors: List[str] = []

    # Parse AST
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        return [f"Syntax error: {e}"]

    # --- Check for generate_signals function ---
    has_generate_signals = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef) and node.name == REQUIRED_FUNCTION:
            has_generate_signals = True
            # Check function signature
            arg_names = [a.arg for a in node.args.args]
            if len(arg_names) < 1:
                errors.append("generate_signals must accept at least 1 argument (df)")

    if not has_generate_signals:
        errors.append(f"Missing required function: '{REQUIRED_FUNCTION}'")

    # --- Walk AST for security violations ---
    for node in ast.walk(tree):
        # Check imports
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_module = alias.name.split(".")[0]
                if top_module in FORBIDDEN_IMPORTS:
                    errors.append(
                        f"Forbidden import: '{alias.name}' "
                        f"(line {getattr(node, 'lineno', '?')})"
                    )

        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_module = node.module.split(".")[0]
                if top_module in FORBIDDEN_IMPORTS:
                    errors.append(
                        f"Forbidden import from: '{node.module}' "
                        f"(line {getattr(node, 'lineno', '?')})"
                    )

        # Check dangerous calls
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                if node.func.id in FORBIDDEN_CALLS:
                    errors.append(
                        f"Forbidden call: '{node.func.id}()' "
                        f"(line {getattr(node, 'lineno', '?')})"
                    )

    # --- Regex patterns for additional checks ---
    for pattern, reason in FORBIDDEN_CALL_PATTERNS:
        if re.search(pattern, source):
            errors.append(f"Forbidden pattern detected: {reason}")

    return errors


# ---------------------------------------------------------------------------
# Quick check: only imports (for early rejection)
# ---------------------------------------------------------------------------


def has_forbidden_imports(source: str) -> bool:
    """Quick check if source has any forbidden imports (no AST needed)."""
    import re
    # Match lines like: import os | from os import ... | import os.path
    import_pattern = re.compile(
        r"^\s*(?:import|from)\s+(" + "|".join(re.escape(m) for m in sorted(FORBIDDEN_IMPORTS, key=len, reverse=True)) + r")(?:\s|\.|$)",
        re.MULTILINE,
    )
    return bool(import_pattern.search(source))
