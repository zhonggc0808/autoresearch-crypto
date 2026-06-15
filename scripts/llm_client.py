#!/usr/bin/env python3
"""Thin LLM client — multi-provider API wrapper.

Supports Anthropic (native) and OpenAI-compatible providers (DeepSeek, etc.).

Usage:
    from scripts.llm_client import call_llm

    response = call_llm(
        system_prompt="You are a helpful assistant.",
        user_message="Generate a candidate spec.",
        model="claude-sonnet-4-6",               # or "deepseek-chat", etc.
    )

Environment (all optional):
    LLM_PROVIDER    — "anthropic" (default) or "openai" (for DeepSeek, etc.)
    LLM_API_KEY     — API key (falls back to ANTHROPIC_API_KEY for compat)
    LLM_BASE_URL    — API base URL, e.g. https://api.deepseek.com
    LLM_MODEL       — default model override
    LLM_CLIENT_MOCK — set to "1" for canned response (testing)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Auto-load .env file from project root (no python-dotenv dependency)
# ---------------------------------------------------------------------------

_PROJECT_DIR = Path(__file__).resolve().parents[1]
_ENV_PATH = _PROJECT_DIR / ".env"
if _ENV_PATH.exists():
    with open(_ENV_PATH, encoding="utf-8") as _fh:
        for _line in _fh:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _val = _line.split("=", 1)
            _key = _key.strip()
            _val = _val.strip().strip("\"'")
            if _key and not os.environ.get(_key):
                os.environ[_key] = _val

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------


def _resolve_provider() -> str:
    """Resolve LLM provider from env var. Default: anthropic."""
    return os.environ.get("LLM_PROVIDER", "anthropic").lower()


def _resolve_api_key() -> str:
    """Resolve API key: LLM_API_KEY, then ANTHROPIC_API_KEY."""
    key = os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError(
            "No API key found. Set LLM_API_KEY or ANTHROPIC_API_KEY."
        )
    return key


def _resolve_base_url(provider: str) -> str:
    """Resolve API base URL from env var or provider default."""
    env_url = os.environ.get("LLM_BASE_URL")
    if env_url:
        return env_url.rstrip("/")
    if provider == "openai":
        return "https://api.deepseek.com"
    return "https://api.anthropic.com"


def _resolve_model(override: str) -> str:
    """Resolve model: env var LLM_MODEL wins, else the caller's override."""
    return os.environ.get("LLM_MODEL") or override


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def call_llm(
    system_prompt: str,
    user_message: str,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
) -> str:
    """Call the LLM API and return the response text.

    Dispatches by provider (LLM_PROVIDER env var):
    - "anthropic" (default): Anthropic Messages API
    - "openai":              OpenAI-compatible API (DeepSeek, Together, etc.)

    Falls back through:
    1. Mock mode (if LLM_CLIENT_MOCK=1)
    2. ``anthropic`` Python SDK (anthropic provider only)
    3. Direct HTTP via ``urllib``
    """
    # --- Mock mode ---
    if os.environ.get("LLM_CLIENT_MOCK") == "1":
        return _mock_response()

    provider = _resolve_provider()
    model = _resolve_model(model)

    # --- Provider-specific dispatch ---
    if provider == "openai":
        return _call_openai_http(system_prompt, user_message, model,
                                  max_tokens, temperature)

    # --- Anthropic SDK ---
    try:
        return _call_anthropic_sdk(system_prompt, user_message, model,
                                    max_tokens, temperature)
    except ImportError:
        pass

    # --- Anthropic direct HTTP ---
    try:
        return _call_anthropic_http(system_prompt, user_message, model,
                                     max_tokens, temperature)
    except Exception as e:
        raise RuntimeError(
            f"LLM call failed (try: pip install anthropic): {e}"
        ) from e


# ---------------------------------------------------------------------------
# Anthropic SDK-based caller
# ---------------------------------------------------------------------------


def _call_anthropic_sdk(
    system_prompt: str, user_message: str, model: str,
    max_tokens: int, temperature: float,
) -> str:
    """Call Anthropic API via the official Python SDK."""
    import anthropic

    api_key = _resolve_api_key()
    client = anthropic.Anthropic(api_key=api_key)

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )

    # Extract text from response
    text_parts = []
    for block in response.content:
        if hasattr(block, "text"):
            text_parts.append(block.text)
        elif isinstance(block, dict) and block.get("type") == "text":
            text_parts.append(block.get("text", ""))
    return "\n".join(text_parts)


# ---------------------------------------------------------------------------
# Anthropic HTTP caller (no SDK required)
# ---------------------------------------------------------------------------


def _call_anthropic_http(
    system_prompt: str, user_message: str, model: str,
    max_tokens: int, temperature: float,
) -> str:
    """Call Anthropic API via direct HTTP request (urllib)."""
    import json as _json
    import urllib.request as _request

    api_key = _resolve_api_key()
    base_url = _resolve_base_url("anthropic")

    body = _json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_message}],
    }).encode("utf-8")

    req = _request.Request(
        f"{base_url}/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with _request.urlopen(req) as resp:
        data = _json.loads(resp.read().decode("utf-8"))

    # Extract text from content blocks
    text_parts = []
    for block in data.get("content", []):
        if block.get("type") == "text":
            text_parts.append(block.get("text", ""))
    return "\n".join(text_parts)


# ---------------------------------------------------------------------------
# OpenAI-compatible HTTP caller (DeepSeek, Together, OpenAI, etc.)
# ---------------------------------------------------------------------------


def _call_openai_http(
    system_prompt: str, user_message: str, model: str,
    max_tokens: int, temperature: float,
) -> str:
    """Call an OpenAI-compatible API via direct HTTP (urllib)."""
    import json as _json
    import urllib.request as _request

    api_key = _resolve_api_key()
    base_url = _resolve_base_url("openai")

    body = _json.dumps({
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }).encode("utf-8")

    req = _request.Request(
        f"{base_url}/v1/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    with _request.urlopen(req) as resp:
        data = _json.loads(resp.read().decode("utf-8"))

    # Extract text from OpenAI-compatible response
    # Structure: choices[0].message.content
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(
            f"Unexpected API response structure: {e}. "
            f"Response keys: {list(data.keys())}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response() -> str:
    """Return a canned candidate JSON for testing."""
    return json.dumps({
        "parent_id": "channel_breakout_v2_1_balanced",
        "description": "Mock: test entry_lookback=300 across all regimes",
        "hypothesis": "Reducing entry_lookback from 375 to 300 across all regimes increases trade frequency by ~15% without degrading DD.",
        "expected_behavior_change": "Trade count increases ~15%, bear regime return stays above 200%, IS DD stays above -50%.",
        "params": {
            "strategy_type": "regime_permission_channel_breakout",
            "regime_change_policy": "permission_based",
            "regime_filter": {"fast_days": 50, "slow_days": 200},
            "bull": {
                "candidate": "U4_cons3_L300",
                "strategy_params": {"entry_lookback": 300, "min_hold_bars": 432,
                                    "enable_long": True, "enable_short": False},
                "permission": {"allow_long": True, "allow_short": False,
                               "close_below_ema_disables_long": True,
                               "ema_fast": 50, "consecutive_below_ema_days": 3},
            },
            "bear": {
                "candidate": "K0_base_L300",
                "strategy_params": {"entry_lookback": 300, "min_hold_bars": 432,
                                    "enable_long": True, "enable_short": True},
                "permission": {"allow_long": True, "allow_short": True},
            },
            "neutral": {
                "candidate": "N3_dir_L300",
                "strategy_params": {"entry_lookback": 300, "min_hold_bars": 432,
                                    "enable_long": True, "enable_short": True},
                "permission": {"allow_long": True, "allow_short": True,
                               "directional_only": True, "ema_fast": 50,
                               "ema_slope_days": 5},
            },
        },
    })
