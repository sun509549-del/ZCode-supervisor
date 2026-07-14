"""Shared Codex usage metric definitions."""

from __future__ import annotations

from typing import Any

PRIMARY_METRIC = "effective_codex_work"
PRIMARY_METRIC_FORMULA = "uncached_input_tokens + output_tokens + reasoning_output_tokens"
METRIC_FORMULA_VERSION = "codex_usage_metrics.v1"
SECONDARY_METRICS = (
    "raw_input",
    "cached_input",
    "uncached_input",
    "output",
    "reasoning",
    "uncached_plus_reasoning",
)
USAGE_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
)
COMPARISON_METRIC_FIELDS = (
    "input_tokens",
    "cached_input_tokens",
    "uncached_input_tokens",
    "output_tokens",
    "reasoning_output_tokens",
    "total_in_out",
    "total_plus_reasoning",
    "uncached_plus_reasoning",
    "effective_codex_work",
)


def token_metric_metadata() -> dict[str, Any]:
    return {
        "primary_metric": PRIMARY_METRIC,
        "primary_metric_formula": PRIMARY_METRIC_FORMULA,
        "metric_formula_version": METRIC_FORMULA_VERSION,
        "secondary_metrics": list(SECONDARY_METRICS),
    }


def compute_usage_metrics(
    *,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_output_tokens: int,
) -> dict[str, int]:
    uncached = max(input_tokens - cached_input_tokens, 0)
    return {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "uncached_input_tokens": uncached,
        "output_tokens": output_tokens,
        "reasoning_output_tokens": reasoning_output_tokens,
        "total_in_out": input_tokens + output_tokens,
        "total_plus_reasoning": input_tokens + output_tokens + reasoning_output_tokens,
        "uncached_plus_reasoning": uncached + reasoning_output_tokens,
        "effective_codex_work": uncached + output_tokens + reasoning_output_tokens,
    }


def unavailable_usage_metrics() -> dict[str, None]:
    return {field: None for field in COMPARISON_METRIC_FIELDS}


def metric_invariant_violations(metrics: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    uncached = metrics.get("uncached_input_tokens")
    output = metrics.get("output_tokens")
    reasoning = metrics.get("reasoning_output_tokens")
    effective = metrics.get("effective_codex_work")
    uncached_plus_reasoning = metrics.get("uncached_plus_reasoning")
    if all(isinstance(value, int) for value in (uncached, output, reasoning, effective)):
        if effective != uncached + output + reasoning:
            violations.append("effective_codex_work formula mismatch")
    if all(isinstance(value, int) for value in (uncached, reasoning, uncached_plus_reasoning)):
        if uncached_plus_reasoning != uncached + reasoning:
            violations.append("uncached_plus_reasoning formula mismatch")
    return violations
