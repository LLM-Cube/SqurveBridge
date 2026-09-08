from __future__ import annotations

from typing import Literal

from pydantic import Field

from .execution import ExecutionResult
from .models import StrictModel


class AttributionResult(StrictModel):
    attribution_protocol: str = "project.rule-attribution@2"
    rule_id: str
    rule_version: str = "1"
    candidate_id: str
    primary_label: str | None
    supporting_evidence_refs: list[str]
    alternative_observed_symptoms: list[str] = Field(default_factory=list)
    unknown_items: list[str] = Field(default_factory=list)
    interpretation: Literal["observed_rule_label"] = "observed_rule_label"


def attribute_execution(result: ExecutionResult, *, parse_success: bool | None,
                        schema_resolution: Literal["resolved", "unresolved", "ambiguous"] | None,
                        execution_correct: bool | None = None,
                        structural_differences: list[str] | None = None,
                        analysis_ref: str | None = None,
                        structural_ref: str | None = None) -> AttributionResult:
    refs = [f"execution:{result.execution_id}"]
    if analysis_ref:
        refs.append(analysis_ref)
    if structural_ref and structural_differences:
        refs.append(structural_ref)
    unknown = []
    alternatives = list(structural_differences or [])
    if parse_success is False:
        rule, label = "parse-failure", "syntax_or_parse"
    elif schema_resolution in {"unresolved", "ambiguous"}:
        rule, label = "schema-resolution", schema_resolution
    elif result.status == "timeout":
        rule, label = "execution-timeout", "timeout"
    elif result.status == "execution_error":
        rule, label = "backend-error", "execution"
    elif result.status == "success" and result.comparable and execution_correct is True:
        rule, label = "observed-success", "success"
    elif result.status == "success" and result.comparable and execution_correct is False:
        rule, label = "result-mismatch", "result_mismatch"
    else:
        rule, label = "insufficient-evidence", None
        if parse_success is None:
            unknown.append("parse_status")
        if schema_resolution is None:
            unknown.append("schema_resolution")
        if execution_correct is None:
            unknown.append("execution_correctness")
    return AttributionResult(rule_id=rule, candidate_id=result.candidate_id, primary_label=label,
                             supporting_evidence_refs=refs,
                             alternative_observed_symptoms=alternatives, unknown_items=unknown)
