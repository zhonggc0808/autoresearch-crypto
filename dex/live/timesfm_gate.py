"""Live forecast gate for blocking weak ChannelBreakout entries."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


def signal_target(signal_id: int, current_position: int) -> int:
    if signal_id == 2:
        return 1
    if signal_id == 3:
        return -1
    if signal_id == 0:
        return 0
    return current_position


def forecast_allows(
    direction: int,
    forecast: dict[str, float],
    min_edge_pct: float,
    risk_floor_pct: float,
) -> bool:
    if direction > 0:
        return (
            forecast["median_return"] > min_edge_pct
            and forecast["q10_return"] > -risk_floor_pct
        )
    return (
        forecast["median_return"] < -min_edge_pct
        and forecast["q90_return"] < risk_floor_pct
    )


def blocked_signal(current_position: int) -> int:
    return 0 if current_position else 1


def moirai2_quantile_forecast(
    quantiles: Any,
    *,
    horizon: int,
    spot: float,
) -> dict[str, float]:
    arr = np.asarray(quantiles, dtype=float)
    q = arr[0]
    vals = q[:, horizon - 1]
    if vals.ndim > 1:
        vals = vals[:, 0]
    return {
        "spot": spot,
        "median_return": float(vals[4] / spot - 1.0),
        "q10_return": float(vals[0] / spot - 1.0),
        "q90_return": float(vals[8] / spot - 1.0),
    }


@dataclass
class TimesFMLiveGate:
    candidate_id: str
    base_variant: str
    strategy_type: str
    model_family: str
    model_id: str
    model_source: str
    context: int
    horizon: int
    min_edge_pct: float
    risk_floor_pct: float
    cache_path: Path
    _model: Any = None
    _cache: dict[str, dict[str, float]] = field(default_factory=dict)

    @classmethod
    def from_candidate(
        cls,
        candidate_path: str,
        model_source: str,
        cache_path: str,
        *,
        checkpoint_variant: str,
    ) -> "TimesFMLiveGate":
        spec = json.loads(Path(candidate_path).read_text(encoding="utf-8"))
        params = spec.get("params", {})
        strategy_type = str(params.get("strategy_type", ""))
        if strategy_type not in {"timesfm_quantile_gate", "moirai2_quantile_gate"}:
            raise ValueError(f"{candidate_path} is not a supported live quantile gate candidate")
        base_variant = str(params.get("base_variant", ""))
        if base_variant != checkpoint_variant:
            raise ValueError(
                f"Gate candidate base_variant={base_variant!r} "
                f"does not match checkpoint variant={checkpoint_variant!r}"
            )
        model_family = "moirai2" if strategy_type == "moirai2_quantile_gate" else "timesfm"
        if model_family == "moirai2":
            model_source = str(params["model_id"])
        model_path = Path(model_source)
        if model_path.exists():
            model_source = str(model_path)
        elif os.sep in model_source or "/" in model_source:
            raise FileNotFoundError(f"Gate model path not found: {model_source}")

        gate = cls(
            candidate_id=str(spec.get("experiment_id", Path(candidate_path).stem)),
            base_variant=base_variant,
            strategy_type=strategy_type,
            model_family=model_family,
            model_id=str(params["model_id"]),
            model_source=model_source,
            context=int(params["context"]),
            horizon=int(params["horizon"]),
            min_edge_pct=float(params["min_edge_pct"]),
            risk_floor_pct=float(params["risk_floor_pct"]),
            cache_path=Path(cache_path),
        )
        gate._cache = gate._load_cache()
        return gate

    def apply(
        self,
        signal_id: int,
        df: pd.DataFrame,
        *,
        current_position: int,
    ) -> tuple[int, dict[str, Any]]:
        target = signal_target(int(signal_id), int(current_position))
        info: dict[str, Any] = {
            "active": True,
            "candidate_id": self.candidate_id,
            "strategy_type": self.strategy_type,
            "model_family": self.model_family,
            "input_signal": int(signal_id),
            "output_signal": int(signal_id),
            "checked": False,
            "allowed": True,
            "reason": "not_entry",
        }
        if target == 0 or target == current_position:
            return int(signal_id), info

        info["checked"] = True
        direction = "long" if target > 0 else "short"
        info["direction"] = direction
        try:
            forecast = self._forecast_latest(df)
            allowed = forecast_allows(target, forecast, self.min_edge_pct, self.risk_floor_pct)
            info.update(forecast)
        except Exception as exc:  # fail closed for new entries
            allowed = False
            info["reason"] = f"forecast_error:{type(exc).__name__}"

        if allowed:
            info["reason"] = "allowed"
            return int(signal_id), info

        output = blocked_signal(current_position)
        info["allowed"] = False
        info["output_signal"] = output
        if info["reason"] == "not_entry":
            info["reason"] = f"blocked_{direction}"
        return output, info

    def _forecast_latest(self, df: pd.DataFrame) -> dict[str, float]:
        if len(df) < 32:
            raise ValueError("not enough bars for forecast")
        close = df["close"].to_numpy(dtype=float)
        spot = float(close[-1])
        key = self._cache_key(df, spot)
        cached = self._cache.get(key)
        if cached:
            return cached

        model = self._load_model()
        inputs = [close[-self.context :].astype(np.float32)]
        if self.model_family == "moirai2":
            forecast = moirai2_quantile_forecast(
                model.predict(inputs),
                horizon=self.horizon,
                spot=spot,
            )
        else:
            point, quantiles = model.forecast(horizon=self.horizon, inputs=inputs)
            forecast = {
                "spot": spot,
                "median_return": float(point[0, self.horizon - 1] / spot - 1.0),
                "q10_return": float(quantiles[0, self.horizon - 1, 1] / spot - 1.0),
                "q90_return": float(quantiles[0, self.horizon - 1, 9] / spot - 1.0),
            }
        self._cache[key] = forecast
        self._save_cache()
        return forecast

    def _cache_key(self, df: pd.DataFrame, spot: float) -> str:
        if "timestamp" in df.columns:
            ts = str(int(df["timestamp"].iloc[-1]))
        elif "datetime" in df.columns:
            ts = str(pd.Timestamp(df["datetime"].iloc[-1]))
        else:
            ts = str(len(df))
        return f"{self.candidate_id}:{ts}:{spot:.8f}:c{self.context}:h{self.horizon}"

    def _load_cache(self) -> dict[str, dict[str, float]]:
        if not self.cache_path.exists():
            return {}
        return json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.cache_path.write_text(
            json.dumps(self._cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_model(self):
        if self._model is not None:
            return self._model
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "")
        import importlib

        import torch

        # ponytail: current local torch may not support newest laptop GPUs; live gate can run CPU.
        torch.cuda.is_available = lambda: False
        torch.set_float32_matmul_precision("high")
        if self.model_family == "moirai2":
            try:
                moirai2 = importlib.import_module("uni2ts.model.moirai2")
            except ImportError as exc:
                raise RuntimeError(
                    "Moirai2 live gate requires uni2ts. "
                    "Run live with: uv run --with uni2ts python <live_script>.py ..."
                ) from exc
            module = moirai2.Moirai2Module.from_pretrained(self.model_source)
            self._model = moirai2.Moirai2Forecast(
                prediction_length=self.horizon,
                target_dim=1,
                feat_dynamic_real_dim=0,
                past_feat_dynamic_real_dim=0,
                context_length=self.context,
                module=module,
            )
            return self._model

        timesfm = importlib.import_module("timesfm")
        model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(self.model_source)
        model.compile(
            timesfm.ForecastConfig(
                max_context=self.context,
                max_horizon=self.horizon,
                normalize_inputs=True,
                per_core_batch_size=1,
                use_continuous_quantile_head=True,
                force_flip_invariance=True,
                infer_is_positive=True,
                fix_quantile_crossing=True,
            )
        )
        self._model = model
        return model
