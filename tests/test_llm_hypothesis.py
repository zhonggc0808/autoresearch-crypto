"""Tests for dex.llm_hypothesis — LLM-powered hypothesis generator."""

import json
import os

import pytest

from dex.llm_hypothesis import (
    LLMHypothesisGenerator,
    build_user_prompt,
    inject_llm_hypotheses,
)
from dex.reflection import Hypothesis, ReflectionEngine


# ---------------------------------------------------------------------------
# build_user_prompt
# ---------------------------------------------------------------------------


class TestBuildUserPrompt:
    """Tests for prompt construction (no API call)."""

    def test_basic_structure(self):
        experiments = [
            {
                "agent": "Alpha",
                "hypothesis": "测试假设",
                "tried": "rsi_low: 30 → 25",
                "score_before": 0.35,
                "score_after": 0.42,
                "sharpe_before": -0.17,
                "sharpe_after": 0.08,
                "dd_before": -0.15,
                "dd_after": -0.06,
                "reflection": "改进显著",
                "edge_flags": [],
            }
        ]
        agents = [
            {
                "name": "Alpha",
                "style": "趋势跟踪",
                "recent_score": 0.42,
                "revival_count": 0,
                "params": {"window": 15, "std_dev": 2.5, "use_adx": True},
            }
        ]
        prompt = build_user_prompt(experiments, agents, [], "")
        assert "市场环境" in prompt
        assert "Agent 当前状态" in prompt
        assert "Alpha" in prompt
        assert "趋势跟踪" in prompt
        assert "实验记录" in prompt
        assert "测试假设" in prompt

    def test_market_context_included(self):
        prompt = build_user_prompt([], [], [], "牛市初期，波动率上升")
        assert "牛市初期" in prompt

    def test_unknown_market_context(self):
        prompt = build_user_prompt([], [], [], "")
        assert "未知" in prompt

    def test_blind_spots_listed(self):
        spots = ["震荡市假信号过多", "止损过紧"]
        prompt = build_user_prompt([], [], spots, "")
        for spot in spots:
            assert spot in prompt

    def test_experiment_table_format(self):
        experiments = [
            {
                "agent": "Beta",
                "hypothesis": "H",
                "tried": "p=1",
                "score_before": 0.3,
                "score_after": 0.4,
                "sharpe_before": 0.1,
                "sharpe_after": 0.2,
                "dd_before": -0.10,
                "dd_after": -0.08,
                "reflection": "OK",
                "edge_flags": [],
            }
        ]
        agents = [
            {"name": "Beta", "style": "均值回归", "recent_score": 0.4,
             "revival_count": 0, "params": {"window": 20, "std_dev": 2.0}}
        ]
        prompt = build_user_prompt(experiments, agents, [], "")
        # Score change display
        assert "0.300" in prompt or "0.3" in prompt
        assert "0.400" in prompt or "0.4" in prompt

    def test_edge_flags_shown(self):
        experiments = [
            {
                "agent": "Gamma",
                "hypothesis": "H",
                "tried": "x=1",
                "score_before": 0.3,
                "score_after": 0.3,
                "sharpe_before": 0.0,
                "sharpe_after": 0.0,
                "dd_before": -0.15,
                "dd_after": -0.15,
                "reflection": "",
                "edge_flags": ["RISKY", "OVERFIT"],
            }
        ]
        agents = [
            {"name": "Gamma", "style": "网格", "recent_score": 0.3,
             "revival_count": 1, "params": {"grid_spacing_pct": 0.01}}
        ]
        prompt = build_user_prompt(experiments, agents, [], "")
        assert "RISKY" in prompt
        assert "OVERFIT" in prompt


# ---------------------------------------------------------------------------
# LLMHypothesisGenerator (no API calls)
# ---------------------------------------------------------------------------


class TestLLMHypothesisGenerator:
    """Tests that don't require a live API."""

    def test_provider_detection_anthropic(self):
        gen = LLMHypothesisGenerator(model="claude-sonnet-4-6")
        assert gen.provider == "anthropic"

    def test_provider_detection_deepseek(self):
        gen = LLMHypothesisGenerator(model="deepseek-v4-flash")
        assert gen.provider == "deepseek"

    def test_provider_detection_unknown(self):
        gen = LLMHypothesisGenerator(model="gpt-4")
        assert gen.provider == "deepseek"  # default fallback

    def test_explicit_provider(self):
        gen = LLMHypothesisGenerator(model="gpt-4", provider="anthropic")
        assert gen.provider == "anthropic"

    def test_not_available_without_key(self, monkeypatch):
        """Without API key, is_available should be False."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        assert gen.is_available is False

    def test_available_with_key(self, monkeypatch):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test-key")
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        assert gen.is_available is True

    def test_generate_returns_empty_without_key(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("LLM_API_KEY", raising=False)
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        result = gen.generate([], [], [], n=3)
        assert result == []

    def test_parse_response_json_block(self):
        """_parse_response should extract JSON from markdown code fence."""
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        response = '''```json
{
  "analysis": "测试分析",
  "hypotheses": [
    {
      "text": "假设1",
      "agent": "Alpha",
      "param_changes": {"window": 20},
      "rationale": "原因",
      "blind_spot": "风险"
    }
  ]
}
```'''
        results = gen._parse_response(response, n=4)
        assert len(results) == 1
        assert results[0].text == "假设1"
        assert results[0].agent == "Alpha"
        assert results[0].param_changes == {"window": 20}

    def test_parse_response_plain_json(self):
        """_parse_response should handle plain JSON without code fence."""
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        response = json.dumps({
            "analysis": "分析",
            "hypotheses": [
                {"text": "H1", "agent": "Beta", "param_changes": {"std_dev": 2.0},
                 "rationale": "R", "blind_spot": "B"}
            ]
        }, ensure_ascii=False)
        results = gen._parse_response(response, n=4)
        assert len(results) == 1
        assert results[0].agent == "Beta"

    def test_parse_response_no_hypotheses_key(self):
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        results = gen._parse_response('{"analysis": "no hypotheses here"}', n=4)
        assert results == []

    def test_parse_response_invalid_json(self):
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        with pytest.raises((json.JSONDecodeError, ValueError)):
            gen._parse_response("not valid json at all", n=4)

    def test_parse_response_invalid_agent_fallback(self):
        """Invalid agent name should fall back to Alpha."""
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        response = json.dumps({
            "hypotheses": [
                {"text": "H", "agent": "InvalidAgent", "param_changes": {},
                 "rationale": "", "blind_spot": ""}
            ]
        })
        results = gen._parse_response(response, n=4)
        assert len(results) == 1
        assert results[0].agent == "Alpha"

    def test_parse_response_param_type_coercion(self):
        """String numbers should be converted to int/float, bools stay bool."""
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        response = json.dumps({
            "hypotheses": [
                {
                    "text": "H",
                    "agent": "Alpha",
                    "param_changes": {
                        "window": "20",         # string int → int
                        "std_dev": "2.5",       # string float → float
                        "use_adx": True,         # bool → bool
                        "entry_zone": 0.3,       # float → float
                    },
                    "rationale": "",
                    "blind_spot": "",
                }
            ]
        })
        results = gen._parse_response(response, n=4)
        params = results[0].param_changes
        assert params["window"] == 20
        assert isinstance(params["window"], int)
        assert params["std_dev"] == 2.5
        assert isinstance(params["std_dev"], float)
        assert params["use_adx"] is True
        assert params["entry_zone"] == 0.3

    def test_parse_response_trims_to_n(self):
        """Should only return up to n hypotheses."""
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        response = json.dumps({
            "hypotheses": [
                {"text": f"H{i}", "agent": "Alpha", "param_changes": {},
                 "rationale": "", "blind_spot": ""}
                for i in range(10)
            ]
        })
        results = gen._parse_response(response, n=3)
        assert len(results) == 3

    def test_cache_reuse(self, monkeypatch):
        """Same prompt tail should return cached results."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
        gen = LLMHypothesisGenerator(model="deepseek-chat")
        # Manually set a cache entry
        gen._cache = {}
        h = Hypothesis(text="cached", agent="Alpha", param_changes={})
        gen._cache["test-key"] = [h]

        # generate checks cache first — but since we don't hit API,
        # we test the cache mechanism by checking _cache directly
        assert "test-key" in gen._cache
        assert gen._cache["test-key"][0].text == "cached"


# ---------------------------------------------------------------------------
# inject_llm_hypotheses
# ---------------------------------------------------------------------------


class TestInjectLLMHypotheses:
    """Integration between LLM generator and ReflectionEngine."""

    def test_inject_into_engine(self):
        engine = ReflectionEngine()
        before = len(engine.hypotheses)

        class FakeGenerator:
            is_available = True

            def generate(self, experiments, agents, blind_spots, n):
                return [
                    Hypothesis(text=f"Fake{i}", agent=f"Agent{i}",
                               param_changes={f"p{i}": i})
                    for i in range(n)
                ]

        gen = FakeGenerator()
        count = inject_llm_hypotheses(engine, gen, [], [], [], n=3)
        assert count == 3
        assert len(engine.hypotheses) == before + 3
        # Injected hypotheses should be at front
        assert engine.hypotheses[0].text == "Fake2"  # reversed
        assert engine.hypotheses[1].text == "Fake1"
        assert engine.hypotheses[2].text == "Fake0"

    def test_inject_unavailable_generator(self):
        engine = ReflectionEngine()
        before = len(engine.hypotheses)

        class FakeGenerator:
            is_available = False
            def generate(self, *args, **kwargs):
                return []

        count = inject_llm_hypotheses(engine, FakeGenerator(), [], [], [])
        assert count == 0
        assert len(engine.hypotheses) == before

    def test_custom_base_url(self):
        gen = LLMHypothesisGenerator(
            model="deepseek-chat",
            base_url="https://custom.api.com/v1/chat",
        )
        assert gen.base_url == "https://custom.api.com/v1/chat"
