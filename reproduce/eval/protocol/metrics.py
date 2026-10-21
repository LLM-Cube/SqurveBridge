from __future__ import annotations

from itertools import combinations
from pydantic import Field

from .comparison import compare_results
from .execution import ExecutionResult
from .models import ComparisonPolicy, MetricResult, StrictModel


def soft_f1(predicted: ExecutionResult, gold: ExecutionResult, *, candidate_id: str,
            policy: ComparisonPolicy) -> MetricResult:
    if not predicted.comparable or not gold.comparable:
        return MetricResult(metric_id="sf1", protocol_id="project.result-soft-f1",
                            protocol_version="2.0.0", status="unavailable", coverage=0,
                            candidate_ids=[candidate_id], candidate_scope="selected",
                            actual_k=1, unavailable_count=1, value_domain="unit_interval",
                            reason="complete_results_required")
    pred = list(predicted.rows or [])
    ref = list(gold.rows or [])
    remaining = list(ref)
    matched = 0
    for row in pred:
        for index, target in enumerate(remaining):
            if compare_results([row], [target], policy).equal:
                matched += 1
                remaining.pop(index)
                break
    precision = matched / len(pred) if pred else float(not ref)
    recall = matched / len(ref) if ref else float(not pred)
    value = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    numerator = 2 * matched
    denominator = len(pred) + len(ref)
    if denominator == 0:
        numerator, denominator = 1, 1
    return MetricResult(metric_id="sf1", protocol_id="project.result-soft-f1",
                        protocol_version="2.0.0", status="available", value=value,
                        numerator=numerator, denominator=denominator, coverage=1,
                        candidate_ids=[candidate_id], candidate_scope="selected", actual_k=1,
                        value_domain="unit_interval",
                        provenance={"precision": precision, "recall": recall,
                                    "comparison_policy": policy.model_dump(mode="json")})


def self_consistency(results: list[ExecutionResult], policy: ComparisonPolicy) -> MetricResult:
    pairs = list(combinations(results, 2))
    comparable_pairs = [(a, b) for a, b in pairs if a.comparable and b.comparable]
    matches = sum(compare_results(a.rows or [], b.rows or [], policy).equal for a, b in comparable_pairs)
    denominator = len(pairs)
    return MetricResult(
        metric_id="self_consistency", protocol_id="project.candidate-self-consistency",
        protocol_version="2.0.0", status="available" if denominator else "unavailable",
        value=matches / denominator if denominator else None,
        numerator=matches if denominator else None, denominator=denominator if denominator else None,
        coverage=len(comparable_pairs) / denominator if denominator else 0,
        candidate_ids=[result.candidate_id for result in results], candidate_scope="observed_pool",
        actual_k=len(results), failed_count=sum(result.status == "execution_error" for result in results),
        unavailable_count=sum(not result.comparable for result in results), value_domain="unit_interval",
        reason=None if denominator else "at_least_two_candidates_required",
        provenance={"comparison_policy": policy.model_dump(mode="json"),
                    "comparable_pair_count": len(comparable_pairs),
                    "unavailable_pair_count": denominator - len(comparable_pairs)},
    )


class TokenObservation(StrictModel):
    protocol_id: str = "provider-reported-token-usage@1"
    stage_id: str
    logical_call_id: str
    attempt_id: str | None = None
    scope: str
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    coverage: float = Field(ge=0, le=1)
    source: str
    reason: str | None = None

    @property
    def total_tokens(self) -> int | None:
        if self.prompt_tokens is None or self.completion_tokens is None:
            return None
        return self.prompt_tokens + self.completion_tokens


def aggregate_token_observations(items: list[TokenObservation]) -> tuple[int | None, float, dict[str, int]]:
    """Aggregate one usage scope per logical call without double counting attempts."""
    groups: dict[str, list[TokenObservation]] = {}
    for item in items:
        groups.setdefault(item.logical_call_id, []).append(item)
    totals: list[int | None] = []
    logical_scopes = 0
    attempt_scopes = 0
    for records in groups.values():
        logical = [item for item in records if item.scope == "logical_call"]
        if logical:
            logical_scopes += 1
            totals.append(logical[0].total_tokens if len(logical) == 1 else None)
            continue
        attempts = [item.total_tokens for item in records if item.scope == "attempt"]
        attempt_scopes += 1
        totals.append(sum(value for value in attempts if value is not None)
                      if attempts and all(value is not None for value in attempts) else None)
    coverage = sum(value is not None for value in totals) / len(totals) if totals else 0
    total = sum(value for value in totals if value is not None) if totals and coverage == 1 else None
    return total, coverage, {"logical_call_count": len(groups),
                             "logical_call_scopes_used": logical_scopes,
                             "attempt_scopes_used": attempt_scopes}


class TimingObservation(StrictModel):
    protocol_id: str = "monotonic-operation-timing@1"
    operation_id: str
    scope: str
    duration_s: float | None = Field(default=None, ge=0)
    inclusive: bool
    clock: str = "monotonic"
    status: str


def legacy_efficiency_metric(metric_id: str, value: float | None, *, candidate_id: str,
                             reason: str | None = None) -> MetricResult:
    return MetricResult(metric_id=metric_id, protocol_id=f"project.legacy-{metric_id}",
                        protocol_version="1", status="available" if value is not None else "unavailable",
                        value=value, coverage=1 if value is not None else 0,
                        candidate_ids=[candidate_id], candidate_scope="selected", actual_k=1,
                        value_domain="non_negative", reason=reason,
                        provenance={"identity": "project_legacy", "native": False})
