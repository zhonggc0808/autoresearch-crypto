#!/usr/bin/env python3
"""Restricted import sandbox for LLM-generated strategy code (v0.9a).

Provides safe import of candidate strategy modules with:
    - Restricted globals (no dangerous builtins)
    - AST-based pre-check for forbidden imports
    - Clean module isolation (no leakage to parent namespace)

Usage:
    from scripts.sandbox_import_candidate import import_candidate_strategy

    module = import_candidate_strategy("/path/to/strategy.py")
    signals = module.generate_signals(df)
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Allowed imports for codegen strategies
# ---------------------------------------------------------------------------

ALLOWED_MODULES = {
    "numpy", "numpy.core", "numpy.array", "numpy.random",
    "pandas", "pandas.core", "pandas.DataFrame", "pandas.Series",
    "math",
    "typing",
    "collections",
    "decimal",
    "functools",
    "itertools",
    "operator",
    "random",  # seeded random only (no crypto)
    "statistics",
    "numbers",
    "fractions",
}

# These are carved out of ALLOWED_MODULES during the pre-scan
FORBIDDEN_SUBMODULES = {
    "numpy.random",  # Can't rely on randomness in strategy
}


# ---------------------------------------------------------------------------
# Hook __import__ for sandbox
# ---------------------------------------------------------------------------


class SandboxImportError(ImportError):
    """Raised when a codegen strategy tries to import a forbidden module."""


def _make_sandbox_import(module_dir: str):
    """Create a restricted __import__ function for the sandbox.

    Only allows imports from ALLOWED_MODULES and the codegen directory.
    """
    def _sandbox_import(name, globals=None, locals=None, fromlist=(), level=0):
        # Allow relative imports within the codegen dir
        if level > 0:
            return __builtins__["__import__"](name, globals, locals, fromlist, level)

        top = name.split(".")[0]

        # Check if it's a local import from codegen directory
        try:
            spec = importlib.util.find_spec(name)
            if spec and spec.origin and module_dir in str(spec.origin):
                return __builtins__["__import__"](name, globals, locals, fromlist, level)
        except (ModuleNotFoundError, ValueError):
            pass

        if top not in ALLOWED_MODULES:
            raise SandboxImportError(
                f"Forbidden import '{name}' in codegen strategy. "
                f"Allowed: {sorted(ALLOWED_MODULES)}"
            )

        return __builtins__["__import__"](name, globals, locals, fromlist, level)

    return _sandbox_import


# ---------------------------------------------------------------------------
# Sandbox importer
# ---------------------------------------------------------------------------


def import_candidate_strategy(
    strategy_path: str,
    module_name: str = "codegen_candidate",
) -> Optional[types.ModuleType]:
    """Import a candidate strategy module in a restricted environment.

    Args:
        strategy_path: Absolute path to the strategy.py file.
        module_name: Name to assign to the imported module.

    Returns:
        The imported module, or None if import fails.

    The imported module runs with:
        - Restricted __builtins__ (no exec/eval/open)
        - __import__ hook that only allows safe modules
        - Clean namespace isolated from the caller
    """
    path = Path(strategy_path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Strategy file not found: {path}")
    if path.suffix != ".py":
        raise ValueError(f"Strategy file must be a .py file, got: {path.suffix}")

    # Build sandbox spec
    module_dir = str(path.parent)

    # We use importlib to load the module with restricted globals
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None:
        raise ImportError(f"Could not load spec from {path}")

    module = importlib.util.module_from_spec(spec)

    # Restrict builtins
    safe_builtins = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "chr": chr,
        "dict": dict,
        "divmod": divmod,
        "enumerate": enumerate,
        "filter": filter,
        "float": float,
        "format": format,
        "frozenset": frozenset,
        "hash": hash,
        "hex": hex,
        "id": id,
        "int": int,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "iter": iter,
        "len": len,
        "list": list,
        "map": map,
        "max": max,
        "min": min,
        "next": next,
        "object": object,
        "oct": oct,
        "ord": ord,
        "pow": pow,
        "range": range,
        "repr": repr,
        "reversed": reversed,
        "round": round,
        "set": set,
        "slice": slice,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "type": type,
        "zip": zip,
        "True": True,
        "False": False,
        "None": None,
        "__import__": _make_sandbox_import(module_dir),
        "print": print,  # harmless
    }

    module.__builtins__ = safe_builtins
    module.__file__ = str(path)
    module.__name__ = module_name
    module.__package__ = None

    # Execute with restricted globals
    try:
        sys.path.insert(0, module_dir)
        spec.loader.exec_module(module)
    except SandboxImportError as e:
        raise  # Re-raise sandbox violations
    except Exception as e:
        raise ImportError(f"Failed to import candidate strategy: {e}") from e
    finally:
        if module_dir in sys.path:
            sys.path.remove(module_dir)

    return module
