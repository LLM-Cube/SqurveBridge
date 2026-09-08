"""Typed, versioned evaluation protocol records.

These models describe observed evaluation facts.  They deliberately keep the
bundle schema version separate from every evaluator's protocol version.
"""

from __future__ import annotations

import math
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Availability(str, Enum):
    AVAILABLE = "available"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"
    INVALID = "invalid"


class Candidate(StrictModel):
    candidate_id: str = Field(min_length=1)
    occurrence_id: str = Field(min_length=1)
    sql: str
    ordinal: int = Field(ge=0)
    parent_candidate_id: str | None = None


class ComparisonPolicy(StrictModel):
    protocol_id: str = "project.result-comparison"
    protocol_version: str = "2.0.0"
    row_order: Literal["preserve", "ignore"] = "preserve"
    duplicate_rows: Literal["preserve"] = "preserve"
    column_order: Literal["preserve", "map_one_to_one"] = "map_one_to_one"
    extra_columns: Literal["reject", "allow"] = "reject"
    absolute_tolerance: float = Field(default=1e-2, ge=0)
    relative_tolerance: float = Field(default=0.0, ge=0)
    nulls_equal: bool = True
    empty_results_equal: bool = True
    max_column_mappings: int = Field(default=10000, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def validate_tolerances(self) -> "ComparisonPolicy":
        if not math.isfinite(self.absolute_tolerance) or not math.isfinite(self.relative_tolerance):
            raise ValueError("comparison tolerances must be finite")
        return self


class MetricResult(StrictModel):
    metric_id: str = Field(min_length=1)
    protocol_id: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    status: Availability
    value: float | None = None
    categorical_counts: dict[str, int] | None = None
    numerator: float | None = Field(default=None, ge=0)
    denominator: int | None = Field(default=None, ge=0)
    coverage: float = Field(ge=0, le=1)
    candidate_ids: list[str] = Field(default_factory=list)
    candidate_scope: Literal["first", "selected", "observed_pool", "stage_pair", "none"] = "none"
    actual_k: int | None = Field(default=None, ge=0)
    failed_count: int = Field(default=0, ge=0)
    unavailable_count: int = Field(default=0, ge=0)
    value_domain: Literal["unit_interval", "non_negative", "unbounded"] = "unbounded"
    reason: str | None = None
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_semantics(self) -> "MetricResult":
        if not math.isfinite(self.coverage):
            raise ValueError("coverage must be finite")
        if self.value is not None and not math.isfinite(self.value):
            raise ValueError("metric value must be finite")
        if self.value_domain == "unit_interval" and self.value is not None and not 0 <= self.value <= 1:
            raise ValueError("metric value must be in [0, 1]")
        if self.value_domain == "non_negative" and self.value is not None and self.value < 0:
            raise ValueError("metric value must be non-negative")
        if (self.value_domain == "unit_interval" and self.numerator is not None
                and self.denominator is not None and self.numerator > self.denominator):
            raise ValueError("numerator cannot exceed denominator")
        if self.categorical_counts is not None and any(value < 0 for value in self.categorical_counts.values()):
            raise ValueError("categorical counts must be non-negative")
        if self.status == Availability.AVAILABLE:
            if self.value is None and self.categorical_counts is None:
                raise ValueError("available metric requires a numeric value or categorical counts")
            if self.value is not None and self.categorical_counts is not None:
                raise ValueError("metric cannot claim numeric and categorical values together")
            if (self.value is not None and self.value_domain == "unit_interval"
                    and (self.numerator is None or self.denominator is None)):
                raise ValueError("available unit-interval metric requires numerator and denominator")
            if self.denominator == 0:
                raise ValueError("available metric cannot have zero denominator")
            if (self.value_domain == "unit_interval" and self.numerator is not None
                    and self.denominator is not None and self.denominator > 0
                    and not math.isclose(self.value, self.numerator / self.denominator,
                                         rel_tol=1e-9, abs_tol=1e-12)):
                raise ValueError("metric value contradicts numerator/denominator")
        elif self.value is not None or self.categorical_counts is not None:
            raise ValueError("non-available metric cannot claim a value")
        return self


class SampleEvaluation(StrictModel):
    instance_id: str
    candidates: list[Candidate]
    selected_candidate_id: str | None = None
    metrics: dict[str, MetricResult]
    execution_results: list[dict[str, Any]] = Field(default_factory=list)
    analyses: dict[str, dict[str, Any]] = Field(default_factory=dict)
    attribution: list[dict[str, Any]] = Field(default_factory=list)
    component_observations: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_identities(self) -> "SampleEvaluation":
        ids = [candidate.candidate_id for candidate in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate_id must be unique within a sample")
        occurrences = [candidate.occurrence_id for candidate in self.candidates]
        if len(occurrences) != len(set(occurrences)):
            raise ValueError("candidate occurrence_id must be unique within a sample")
        if [candidate.ordinal for candidate in self.candidates] != list(range(len(self.candidates))):
            raise ValueError("candidate ordinals must be contiguous and ordered")
        known = set(ids)
        if self.selected_candidate_id is not None and self.selected_candidate_id not in known:
            raise ValueError("selected candidate is absent from candidate pool")
        for key, metric in self.metrics.items():
            if key != metric.metric_id:
                raise ValueError("metric mapping key must equal metric_id")
            if not set(metric.candidate_ids).issubset(known):
                raise ValueError("metric references unknown candidate")
            if metric.candidate_scope == "selected":
                expected = [self.selected_candidate_id] if self.selected_candidate_id else []
                if metric.candidate_ids != expected:
                    raise ValueError("selected-candidate metric must reference the recorded selection")
        return self


class CohortDescriptor(StrictModel):
    dataset: str
    split: str
    database_snapshot: str
    sample_ids: list[str]
    candidate_protocol: str
    evaluator_protocol: str
    model_config_id: str = Field(alias="model_config", serialization_alias="model_config")


class EvaluationBundleV2(StrictModel):
    schema_version: Literal[2] = 2
    bundle_protocol: Literal["squrvebridge.evaluation-bundle"] = "squrvebridge.evaluation-bundle"
    bundle_protocol_version: str = "2.0.0"
    run_id: str
    dataset: str
    split: str
    method: str
    database_snapshot: str = "unknown"
    candidate_protocol: str = "ordered-observed-candidates@1"
    evaluator_protocol: str = "project-evaluation@2"
    model_config_id: str = "unknown"
    samples: list[SampleEvaluation]
    aggregate: dict[str, MetricResult] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_aggregates(self) -> "EvaluationBundleV2":
        instance_ids = [sample.instance_id for sample in self.samples]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("instance_id must be unique within a bundle")
        for key, metric in self.aggregate.items():
            if key != metric.metric_id:
                raise ValueError("aggregate mapping key must equal metric_id")
        sample_metric_ids = {metric_id for sample in self.samples for metric_id in sample.metrics}
        missing = sample_metric_ids - set(self.aggregate)
        if missing:
            raise ValueError(f"aggregate metrics missing: {sorted(missing)}")
        for metric_id in sample_metric_ids:
            records = [sample.metrics[metric_id] for sample in self.samples if metric_id in sample.metrics]
            available = [record for record in records if record.status == Availability.AVAILABLE]
            aggregate = self.aggregate[metric_id]
            identities = {
                (record.protocol_id, record.protocol_version, record.candidate_scope, record.value_domain)
                for record in records
            }
            if len(identities) != 1:
                raise ValueError(f"{metric_id}: sample metric protocol identities differ")
            expected_identity = next(iter(identities))
            aggregate_identity = (
                aggregate.protocol_id, aggregate.protocol_version,
                aggregate.candidate_scope, aggregate.value_domain,
            )
            if aggregate_identity != expected_identity:
                raise ValueError(f"{metric_id}: aggregate protocol identity contradicts samples")
            expected_coverage = sum(record.coverage for record in records) / len(records)
            if not math.isclose(aggregate.coverage, expected_coverage, abs_tol=1e-12):
                raise ValueError(f"{metric_id}: aggregate coverage contradicts sample statuses")
            if available:
                categorical = [record for record in available if record.categorical_counts is not None]
                if categorical:
                    if len(categorical) != len(available):
                        raise ValueError(f"{metric_id}: sample metric value kinds differ")
                    expected_counts: dict[str, int] = {}
                    for record in categorical:
                        for label, count in (record.categorical_counts or {}).items():
                            expected_counts[label] = expected_counts.get(label, 0) + count
                    if (aggregate.status != Availability.AVAILABLE
                            or aggregate.categorical_counts != expected_counts):
                        raise ValueError(f"{metric_id}: aggregate categorical counts contradict samples")
                else:
                    expected = sum(record.value or 0 for record in available) / len(available)
                    if aggregate.status != Availability.AVAILABLE or aggregate.value is None:
                        raise ValueError(f"{metric_id}: aggregate availability contradicts samples")
                    if not math.isclose(aggregate.value, expected, rel_tol=1e-9, abs_tol=1e-12):
                        raise ValueError(f"{metric_id}: aggregate value contradicts samples")
                if aggregate.denominator != len(available):
                    raise ValueError(f"{metric_id}: aggregate denominator contradicts samples")
            elif aggregate.status == Availability.AVAILABLE:
                raise ValueError(f"{metric_id}: aggregate cannot be available without sample values")
        return self

    @classmethod
    def minimal(
        cls, run_id: str, dataset: str, split: str, method: str,
        instance_id: str, candidates: list[Candidate], metrics: dict[str, MetricResult],
        *, selected_candidate_id: str | None = None,
    ) -> "EvaluationBundleV2":
        aggregate: dict[str, MetricResult] = {}
        for metric_id, record in metrics.items():
            is_available = record.status == Availability.AVAILABLE
            aggregate[metric_id] = MetricResult(
                metric_id=metric_id,
                protocol_id=record.protocol_id,
                protocol_version=record.protocol_version,
                status="available" if is_available else "unavailable",
                value=record.value if is_available else None,
                categorical_counts=record.categorical_counts if is_available else None,
                numerator=(record.value if is_available and record.categorical_counts is None else None),
                denominator=1 if is_available else None,
                coverage=record.coverage,
                candidate_scope=record.candidate_scope,
                failed_count=record.failed_count,
                unavailable_count=0 if is_available else 1,
                value_domain=record.value_domain,
                reason=None if is_available else "no_evaluable_samples",
            )
        return cls(
            run_id=run_id, dataset=dataset, split=split, method=method,
            samples=[SampleEvaluation(instance_id=instance_id, candidates=candidates,
                                      selected_candidate_id=selected_candidate_id, metrics=metrics)],
            aggregate=aggregate,
        )

    def cohort(self) -> CohortDescriptor:
        return CohortDescriptor(
            dataset=self.dataset, split=self.split, database_snapshot=self.database_snapshot,
            sample_ids=[sample.instance_id for sample in self.samples],
            candidate_protocol=self.candidate_protocol,
            evaluator_protocol=self.evaluator_protocol,
            model_config=self.model_config_id,
        )
