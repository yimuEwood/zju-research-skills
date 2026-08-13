#!/usr/bin/env python3
"""Pool compatible independent effects with explicit meta-analysis model choices."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import NormalDist
from typing import Any


TAU_ESTIMATORS = {"reml", "dersimonian_laird"}
INFERENCE_METHODS = {"normal", "hartung_knapp", "hartung_knapp_modified"}


def _number(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _variance(row: dict[str, Any]) -> float:
    if row.get("standard_error") not in (None, ""):
        se = _number(row["standard_error"], "standard_error")
        if se <= 0:
            raise ValueError("standard_error must be positive")
        return se * se
    lower = _number(row.get("ci_lower"), "ci_lower")
    upper = _number(row.get("ci_upper"), "ci_upper")
    level = _number(row.get("confidence_level", 0.95), "confidence_level")
    if not 0 < level < 1 or upper <= lower:
        raise ValueError("confidence interval or confidence_level is invalid")
    z = NormalDist().inv_cdf(0.5 + level / 2)
    return ((upper - lower) / (2 * z)) ** 2


def _betacf(a: float, b: float, x: float) -> float:
    """Evaluate the incomplete-beta continued fraction (Numerical Recipes form)."""
    maximum_iterations = 300
    epsilon = 3e-14
    floor = 1e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    d = floor if abs(d) < floor else d
    d = 1.0 / d
    value = d
    for iteration in range(1, maximum_iterations + 1):
        even = 2 * iteration
        aa = iteration * (b - iteration) * x / ((qam + even) * (a + even))
        d = 1.0 + aa * d
        d = floor if abs(d) < floor else d
        c = 1.0 + aa / c
        c = floor if abs(c) < floor else c
        d = 1.0 / d
        value *= d * c
        aa = -(a + iteration) * (qab + iteration) * x / ((a + even) * (qap + even))
        d = 1.0 + aa * d
        d = floor if abs(d) < floor else d
        c = 1.0 + aa / c
        c = floor if abs(c) < floor else c
        d = 1.0 / d
        delta = d * c
        value *= delta
        if abs(delta - 1.0) <= epsilon:
            return value
    raise ArithmeticError("incomplete beta continued fraction did not converge")


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def _student_t_cdf(value: float, degrees_of_freedom: int) -> float:
    if degrees_of_freedom <= 0:
        raise ValueError("Student-t degrees of freedom must be positive")
    if value == 0:
        return 0.5
    x = degrees_of_freedom / (degrees_of_freedom + value * value)
    tail = 0.5 * _regularized_beta(x, degrees_of_freedom / 2.0, 0.5)
    return 1.0 - tail if value > 0 else tail


def _student_t_critical(level: float, degrees_of_freedom: int) -> float:
    target = 0.5 + level / 2.0
    lower, upper = 0.0, 1.0
    while _student_t_cdf(upper, degrees_of_freedom) < target:
        upper *= 2.0
        if upper > 1e8:
            raise ArithmeticError("could not bracket Student-t quantile")
    for _ in range(100):
        middle = (lower + upper) / 2.0
        if _student_t_cdf(middle, degrees_of_freedom) < target:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2.0


def _weighted_mean(effects: list[float], variances: list[float], tau2: float) -> tuple[float, list[float]]:
    weights = [1.0 / (variance + tau2) for variance in variances]
    estimate = sum(weight * effect for weight, effect in zip(weights, effects)) / sum(weights)
    return estimate, weights


def _dersimonian_laird(effects: list[float], variances: list[float]) -> float:
    estimate, weights = _weighted_mean(effects, variances, 0.0)
    q = sum(weight * (effect - estimate) ** 2 for weight, effect in zip(weights, effects))
    c = sum(weights) - sum(weight * weight for weight in weights) / sum(weights)
    return max(0.0, (q - (len(effects) - 1)) / c) if c > 0 else 0.0


def _reml_score(tau2: float, effects: list[float], variances: list[float]) -> float:
    estimate, weights = _weighted_mean(effects, variances, tau2)
    sum_weights = sum(weights)
    residual_term = sum(
        weight * weight * (effect - estimate) ** 2
        for weight, effect in zip(weights, effects)
    )
    information_term = sum_weights - sum(weight * weight for weight in weights) / sum_weights
    return residual_term - information_term


def _reml(effects: list[float], variances: list[float]) -> float:
    """Solve the one-parameter REML score equation by deterministic bisection."""
    score_at_zero = _reml_score(0.0, effects, variances)
    if score_at_zero <= 0:
        return 0.0
    upper = max(max(variances), 1e-12)
    for _ in range(100):
        if _reml_score(upper, effects, variances) <= 0:
            break
        upper *= 2.0
    else:
        raise ArithmeticError("REML tau-squared root could not be bracketed")
    lower = 0.0
    for _ in range(120):
        middle = (lower + upper) / 2.0
        if _reml_score(middle, effects, variances) > 0:
            lower = middle
        else:
            upper = middle
    return (lower + upper) / 2.0


def _normal_summary(effects: list[float], variances: list[float], tau2: float, level: float) -> dict[str, float]:
    estimate, weights = _weighted_mean(effects, variances, tau2)
    se = math.sqrt(1.0 / sum(weights))
    critical = NormalDist().inv_cdf(0.5 + level / 2.0)
    return {
        "estimate": estimate,
        "standard_error": se,
        "ci_lower": estimate - critical * se,
        "ci_upper": estimate + critical * se,
        "critical_value": critical,
    }


def _random_summary(
    effects: list[float], variances: list[float], tau2: float, level: float, inference: str
) -> dict[str, Any]:
    estimate, weights = _weighted_mean(effects, variances, tau2)
    df = len(effects) - 1
    residual_scale = sum(
        weight * (effect - estimate) ** 2
        for weight, effect in zip(weights, effects)
    ) / df
    if inference == "normal":
        variance_of_mean = 1.0 / sum(weights)
        critical = NormalDist().inv_cdf(0.5 + level / 2.0)
        scale_used = 1.0
    else:
        scale_used = max(1.0, residual_scale) if inference == "hartung_knapp_modified" else residual_scale
        variance_of_mean = scale_used / sum(weights)
        critical = _student_t_critical(level, df)
    se = math.sqrt(max(0.0, variance_of_mean))
    return {
        "estimate": estimate,
        "standard_error": se,
        "ci_lower": estimate - critical * se,
        "ci_upper": estimate + critical * se,
        "critical_value": critical,
        "inference_method": inference,
        "degrees_of_freedom": None if inference == "normal" else df,
        "hartung_knapp_residual_scale": residual_scale if inference != "normal" else None,
        "hartung_knapp_scale_used": scale_used if inference != "normal" else None,
    }


def _prediction_interval(
    summary: dict[str, Any], tau2: float, level: float, k: int, inference: str
) -> dict[str, Any]:
    if inference != "normal" and k < 3:
        return {
            "status": "not_estimable",
            "reason": "Student-t prediction interval requires at least three independent studies",
        }
    if inference == "normal":
        critical = NormalDist().inv_cdf(0.5 + level / 2.0)
        df = None
        method = "normal_approximation"
    else:
        df = k - 2
        critical = _student_t_critical(level, df)
        method = "student_t_k_minus_2"
    prediction_se = math.sqrt(tau2 + summary["standard_error"] ** 2)
    return {
        "status": "estimated",
        "lower": summary["estimate"] - critical * prediction_se,
        "upper": summary["estimate"] + critical * prediction_se,
        "standard_error": prediction_se,
        "critical_value": critical,
        "degrees_of_freedom": df,
        "method": method,
    }


def _warning(code: str, severity: str, message: str) -> dict[str, str]:
    return {"code": code, "severity": severity, "message": message}


def pool(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("effects", [])
    if not isinstance(rows, list) or len(rows) < 2:
        raise ValueError("effects must contain at least two study estimates")
    measure = str(payload.get("effect_measure") or "").strip()
    estimand = str(payload.get("estimand") or "").strip()
    outcome = str(payload.get("outcome") or "").strip()
    time_point = str(payload.get("time_point") or "").strip()
    analysis_scale = str(payload.get("analysis_scale") or "").strip()
    if not all((measure, estimand, outcome, time_point, analysis_scale)):
        raise ValueError("effect_measure, estimand, outcome, time_point, and analysis_scale are required")
    if analysis_scale not in {"identity", "log", "fisher_z"}:
        raise ValueError("analysis_scale must be identity, log, or fisher_z")
    measure_key = measure.lower().replace("-", "_").replace(" ", "_")
    if ("ratio" in measure_key or measure_key in {"rr", "or", "hr"}) and analysis_scale != "log":
        raise ValueError("ratio measures must be supplied on the log analysis scale")
    if measure_key in {"correlation", "pearson_r", "spearman_r", "r"} and analysis_scale != "fisher_z":
        raise ValueError("correlations must be supplied on the Fisher-z analysis scale")
    if payload.get("independent_estimates") is not True:
        raise ValueError("independent_estimates must be explicitly true; dependent effects require a different model")
    tau_estimator = str(payload.get("tau_squared_estimator") or "").strip().lower()
    inference = str(payload.get("random_effects_inference") or "").strip().lower()
    if tau_estimator not in TAU_ESTIMATORS:
        raise ValueError("tau_squared_estimator must be explicitly set to reml or dersimonian_laird")
    if inference not in INFERENCE_METHODS:
        raise ValueError(
            "random_effects_inference must be explicitly set to normal, hartung_knapp, or hartung_knapp_modified"
        )
    level = _number(payload.get("confidence_level", 0.95), "confidence_level")
    if not 0 < level < 1:
        raise ValueError("confidence_level must be between zero and one")

    study_ids: set[str] = set()
    effects: list[float] = []
    variances: list[float] = []
    for index, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            raise ValueError(f"effects[{index}] must be an object")
        study_id = str(row.get("study_id") or "").strip()
        if not study_id or study_id in study_ids:
            raise ValueError("study_id values must be present and unique")
        study_ids.add(study_id)
        for field, expected in (
            ("effect_measure", measure), ("estimand", estimand), ("outcome", outcome),
            ("time_point", time_point), ("analysis_scale", analysis_scale),
        ):
            if str(row.get(field) or "").strip() != expected:
                raise ValueError(f"{study_id} has incompatible {field}")
        effects.append(_number(row.get("effect_estimate"), "effect_estimate"))
        variances.append(_variance(row))

    fixed = _normal_summary(effects, variances, 0.0, level)
    fixed_weights = [1.0 / variance for variance in variances]
    q = sum(
        weight * (effect - fixed["estimate"]) ** 2
        for weight, effect in zip(fixed_weights, effects)
    )
    df = len(effects) - 1
    i2 = max(0.0, (q - df) / q) * 100.0 if q > 0 else 0.0
    tau2 = _reml(effects, variances) if tau_estimator == "reml" else _dersimonian_laird(effects, variances)
    random = _random_summary(effects, variances, tau2, level, inference)
    random["tau_squared"] = tau2
    random["tau_squared_estimator"] = tau_estimator
    random["prediction_interval"] = _prediction_interval(random, tau2, level, len(effects), inference)

    warnings: list[dict[str, str]] = []
    if len(effects) < 5:
        warnings.append(_warning(
            "small_k", "high",
            "Fewer than five studies: tau-squared, random-effects confidence intervals, and prediction intervals are highly uncertain.",
        ))
    elif len(effects) < 10:
        warnings.append(_warning(
            "limited_k", "moderate",
            "Fewer than ten studies: heterogeneity and prediction-interval estimates may be unstable.",
        ))
    if i2 >= 75:
        warnings.append(_warning(
            "high_heterogeneity", "high",
            "I-squared is at least 75%; investigate compatibility and prespecified moderators before interpreting an average effect.",
        ))
    elif i2 >= 50:
        warnings.append(_warning(
            "substantial_heterogeneity", "moderate",
            "I-squared is at least 50%; interpret the pooled average together with heterogeneity and the prediction interval.",
        ))
    if tau_estimator == "dersimonian_laird":
        warnings.append(_warning(
            "dl_tau_squared", "moderate",
            "DerSimonian-Laird can underestimate between-study variance, especially with few or heterogeneous studies; compare REML as a sensitivity analysis.",
        ))
    if inference == "normal":
        warnings.append(_warning(
            "normal_random_effects_inference", "moderate",
            "Normal random-effects inference does not account for uncertainty in tau-squared; compare Hartung-Knapp when scientifically appropriate.",
        ))
    if inference == "hartung_knapp" and random["hartung_knapp_residual_scale"] < 1:
        warnings.append(_warning(
            "unmodified_hk_narrowing", "moderate",
            "Unmodified Hartung-Knapp used a residual scale below one and may yield a narrower interval; run the modified method as sensitivity analysis.",
        ))

    transform = math.exp if analysis_scale == "log" else math.tanh if analysis_scale == "fisher_z" else None
    transformed = None
    if transform:
        transformed = {
            "fixed_effect": {field: transform(fixed[field]) for field in ("estimate", "ci_lower", "ci_upper")},
            "random_effects": {field: transform(random[field]) for field in ("estimate", "ci_lower", "ci_upper")},
            "transform": "exp" if analysis_scale == "log" else "tanh",
        }
        prediction = random["prediction_interval"]
        if prediction["status"] == "estimated":
            transformed["random_effects_prediction_interval"] = {
                "lower": transform(prediction["lower"]),
                "upper": transform(prediction["upper"]),
            }

    return {
        "schema_version": "2.0",
        "model_selection": {
            "tau_squared_estimator": tau_estimator,
            "random_effects_inference": inference,
            "selection_was_explicit": True,
            "note": "No model is universally preferred; justify choices prospectively and run sensitivity analyses when conclusions are model-dependent.",
        },
        "effect_measure": measure,
        "estimand": estimand,
        "outcome": outcome,
        "time_point": time_point,
        "analysis_scale": analysis_scale,
        "confidence_level": level,
        "study_ids": sorted(study_ids),
        "k": len(effects),
        "fixed_effect": fixed,
        "random_effects": random,
        "back_transformed": transformed,
        "heterogeneity": {
            "q": q,
            "degrees_of_freedom": df,
            "i_squared_percent": i2,
            "tau_squared": tau2,
            "tau_squared_estimator": tau_estimator,
        },
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = pool(json.loads(args.input.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError, ValueError, ArithmeticError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
