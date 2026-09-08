from __future__ import annotations

from pydantic import Field

from .models import CohortDescriptor, StrictModel


class CohortComparison(StrictModel):
    compatible: bool
    reasons: list[str]
    common_sample_ids: list[str]
    common_set_size: int = Field(ge=0)
    exclusions: dict[str, int]


def compare_bundles(left: CohortDescriptor, right: CohortDescriptor,
                    *, allowed_differences: set[str] | None = None) -> CohortComparison:
    allowed = set(allowed_differences or set())
    if "model_config" in allowed:
        allowed.remove("model_config")
        allowed.add("model_config_id")
    valid_dimensions = {"dataset", "split", "database_snapshot", "candidate_protocol",
                        "evaluator_protocol", "model_config_id", "sample_ids"}
    unknown_dimensions = allowed - valid_dimensions
    if unknown_dimensions:
        raise ValueError(f"unknown allowed cohort differences: {sorted(unknown_dimensions)}")
    reasons = []
    for field in ("dataset", "split", "database_snapshot", "candidate_protocol",
                  "evaluator_protocol", "model_config_id"):
        if getattr(left, field) in {"", "unknown", None} or getattr(right, field) in {"", "unknown", None}:
            public_name = "model_config" if field == "model_config_id" else field
            reasons.append(f"{public_name}_unavailable")
    for field in ("dataset", "split", "database_snapshot", "candidate_protocol",
                  "evaluator_protocol", "model_config_id"):
        if field not in allowed and getattr(left, field) != getattr(right, field):
            public_name = "model_config" if field == "model_config_id" else field
            reasons.append(f"{public_name}_mismatch")
    common = sorted(set(left.sample_ids) & set(right.sample_ids))
    exclusions = {
        "left_only": len(set(left.sample_ids) - set(right.sample_ids)),
        "right_only": len(set(right.sample_ids) - set(left.sample_ids)),
    }
    if not common:
        reasons.append("empty_sample_intersection")
    if "sample_ids" not in allowed and set(left.sample_ids) != set(right.sample_ids):
        reasons.append("sample_ids_mismatch")
    return CohortComparison(compatible=not reasons, reasons=reasons, common_sample_ids=common,
                            common_set_size=len(common), exclusions=exclusions)
