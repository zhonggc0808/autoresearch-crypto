"""Tests for dex.reflection — ReflectionEngine, Hypothesis, ExperimentLog."""

import pytest
from dex.reflection import (
    ExperimentLog,
    Hypothesis,
    ReflectionEngine,
)


class TestHypothesis:
    """Hypothesis dataclass."""

    def test_defaults(self):
        h = Hypothesis()
        assert h.text == ""
        assert h.agent == ""
        assert h.param_changes == {}
        assert h.rationale == ""
        assert h.blind_spot == ""

    def test_full_hypothesis(self):
        h = Hypothesis(
            text="收紧止损减少亏损",
            agent="Alpha",
            param_changes={"atr_multiplier": 1.5},
            rationale="当前止损太宽",
            blind_spot="可能错过趋势反转",
        )
        assert h.agent == "Alpha"
        assert h.param_changes["atr_multiplier"] == 1.5


class TestExperimentLog:
    """ExperimentLog dataclass."""

    def test_defaults(self):
        log = ExperimentLog()
        assert log.agent == ""
        assert log.score_before == 0.0
        assert log.score_after == 0.0

    def test_timestamp_auto_populated(self):
        log = ExperimentLog()
        assert log.timestamp != ""

    def test_full_log(self):
        log = ExperimentLog(
            agent="Beta",
            hypothesis="放宽入场条件",
            tried="entry_zone: 0.0 → 0.3",
            params_before={"entry_zone": 0.0},
            params_after={"entry_zone": 0.3},
            score_before=0.45,
            score_after=0.52,
            sharpe_before=0.5,
            sharpe_after=0.8,
            ret_before=0.08,
            ret_after=0.12,
            dd_before=-0.10,
            dd_after=-0.07,
            reflection="改进显著",
            edge_flags=["RISKY"],
        )
        assert log.agent == "Beta"
        assert log.score_after > log.score_before
        assert "RISKY" in log.edge_flags


class TestReflectionEngine:
    """ReflectionEngine core operations."""

    @pytest.fixture
    def engine(self):
        return ReflectionEngine()

    @pytest.fixture
    def sample_hypothesis(self):
        return Hypothesis(
            text="测试假设",
            agent="Alpha",
            param_changes={"window": 20},
            rationale="测试",
        )

    @pytest.fixture
    def sample_evaluate_fn(self):
        """Simple evaluate_fn for testing."""
        def fn(params):
            # Score = f(window), higher window = higher score (for testing)
            w = params.get("window", 10)
            score = 0.5 + (w - 10) * 0.01
            sharpe = score - 0.2
            ret = score * 0.1
            dd = -0.1 + (w - 10) * 0.001
            return (score, sharpe, ret, dd)
        return fn

    # --- Initial state ---

    def test_initial_state(self, engine):
        assert len(engine.experiment_logs) == 0
        assert len(engine.hypotheses) == 8  # 8 default templates
        assert len(engine.meta_reflections) == 0
        assert len(engine.blind_spots) == 0

    # --- inject_hypotheses ---

    def test_inject_empty(self, engine):
        engine.inject_hypotheses([])
        assert len(engine.hypotheses) == 8

    def test_inject_none(self, engine):
        engine.inject_hypotheses(None)  # type: ignore
        assert len(engine.hypotheses) == 8

    def test_inject_add_to_front(self, engine):
        new_h = [
            Hypothesis(text="LLM假设1", agent="Alpha", param_changes={"x": 1}),
            Hypothesis(text="LLM假设2", agent="Beta", param_changes={"y": 2}),
        ]
        engine.inject_hypotheses(new_h)
        # New hypotheses should be at the front
        assert engine.hypotheses[0].text == "LLM假设2"  # reversed order
        assert engine.hypotheses[1].text == "LLM假设1"
        assert len(engine.hypotheses) == 10

    # --- run_experiment ---

    def test_run_experiment_basic(self, engine, sample_hypothesis, sample_evaluate_fn):
        params_before = {"window": 10}
        log = engine.run_experiment(
            agent_name="Alpha",
            hypothesis=sample_hypothesis,
            params_before=params_before,
            evaluate_fn=sample_evaluate_fn,
        )
        assert log.agent == "Alpha"
        assert log.params_before == params_before
        assert log.params_after["window"] == 20  # hypothesis was applied
        assert log.score_after > log.score_before  # higher window = higher score
        assert log.reflection != ""  # auto-generated
        assert len(engine.experiment_logs) == 1

    def test_run_experiment_logs_appended(self, engine, sample_hypothesis, sample_evaluate_fn):
        engine.run_experiment("Alpha", sample_hypothesis, {"window": 10}, sample_evaluate_fn)
        engine.run_experiment("Beta", sample_hypothesis, {"window": 15}, sample_evaluate_fn)
        assert len(engine.experiment_logs) == 2

    def test_run_experiment_hypothesis_with_expression(self, engine, sample_evaluate_fn):
        """Hypothesis param_changes can have string expressions like 'current+5'."""
        h = Hypothesis(text="动态调整", agent="Alpha", param_changes={"window": "current+5"})
        log = engine.run_experiment("Alpha", h, {"window": 10}, sample_evaluate_fn)
        assert log.params_after["window"] == 15

    def test_run_experiment_reflection_mentions_improvement(self, engine, sample_evaluate_fn):
        h = Hypothesis(text="改善测试", agent="Alpha", param_changes={"window": 25})
        log = engine.run_experiment("Alpha", h, {"window": 10}, sample_evaluate_fn)
        # With big improvement, reflection should mention it
        assert "改进" in log.reflection or "改善" in log.reflection

    def test_run_experiment_reflection_mentions_decline(self, engine, sample_evaluate_fn):
        h = Hypothesis(text="恶化测试", agent="Alpha", param_changes={"window": 5})
        log = engine.run_experiment("Alpha", h, {"window": 10}, sample_evaluate_fn)
        # With decline, reflection should mention it
        assert "恶化" in log.reflection or "下降" in log.reflection or "变差" in log.reflection

    # --- meta_reflect ---

    def test_meta_reflect_empty(self, engine):
        """Meta-reflect with no experiments should work but return empty."""
        summary, new_hypotheses = engine.meta_reflect()
        assert isinstance(summary, str)
        assert isinstance(new_hypotheses, list)
        # New hypotheses should be appended to pool
        assert len(engine.hypotheses) >= 8

    def test_meta_reflect_with_experiments(self, engine, sample_evaluate_fn):
        """Meta-reflect after running some experiments."""
        for i in range(5):
            h = Hypothesis(text=f"实验{i}", agent="Alpha", param_changes={"window": 12})
            engine.run_experiment("Alpha", h, {"window": 10}, sample_evaluate_fn)

        before_count = len(engine.hypotheses)
        summary, new_h = engine.meta_reflect()
        assert len(engine.hypotheses) > before_count
        assert len(engine.meta_reflections) == 1
        # Blind spots may be detected
        assert isinstance(engine.blind_spots, list)

    # --- State persistence (stub) ---

    def test_save_load_state(self, engine, tmp_path):
        """Save and load engine state to/from JSON."""
        # Run an experiment first
        h = Hypothesis(text="测试", agent="Alpha", param_changes={"x": 1})
        def fn(p):
            return (0.5, 0.3, 0.05, -0.05)
        engine.run_experiment("Alpha", h, {"x": 0}, fn)

        # Save
        path = tmp_path / "state.json"
        engine.save_state(str(path))
        assert path.exists()

        # Load into new engine
        engine2 = ReflectionEngine()
        engine2.load_state(str(path))
        assert len(engine2.experiment_logs) == 1
        assert engine2.experiment_logs[0].agent == "Alpha"

    def test_load_state_missing_file(self, engine):
        """Loading a missing file should not crash."""
        engine.load_state("/nonexistent/path.json")
        # Engine should remain in valid state
        assert len(engine.experiment_logs) == 0
