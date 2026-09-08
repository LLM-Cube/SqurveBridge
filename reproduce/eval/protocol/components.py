from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .models import StrictModel


class ComponentTransition(StrictModel):
    protocol_id: str = "project.component-transition@2"
    stage_id: str
    task_id: str
    instance_id: str
    candidate_id: str
    iteration: int = Field(ge=0)
    status: Literal["observed", "unknown"]
    before_checkpoint: str | None
    after_checkpoint: str | None
    eligible_pairs: int = 0
    wrong_to_right: int | None = None
    right_to_wrong: int | None = None
    net_ex_change: float | None = None
    before_correct: bool | None = None
    after_correct: bool | None = None

    @model_validator(mode="after")
    def validate_observation(self) -> "ComponentTransition":
        values = (self.wrong_to_right, self.right_to_wrong, self.net_ex_change)
        if self.status == "observed":
            if not self.before_checkpoint or not self.after_checkpoint:
                raise ValueError("observed transition requires both immediate checkpoints")
            if self.before_correct is None or self.after_correct is None or any(v is None for v in values):
                raise ValueError("observed transition requires complete correctness facts")
            if self.eligible_pairs != 1:
                raise ValueError("an observed transition represents exactly one eligible pair")
        elif self.eligible_pairs or any(v is not None for v in values):
            raise ValueError("unknown transition cannot claim transition counts")
        return self


def evaluate_component_transition(*, stage_id: str, task_id: str, instance_id: str,
                                  candidate_id: str,
                                  iteration: int, before_correct: bool | None, after_correct: bool | None,
                                  before_checkpoint: str | None, after_checkpoint: str | None
                                  ) -> ComponentTransition:
    for name, value in (("before_correct", before_correct), ("after_correct", after_correct)):
        if value is not None and not isinstance(value, bool):
            raise ValueError(f"{name} must be bool or None")
    if not before_checkpoint or not after_checkpoint or before_correct is None or after_correct is None:
        return ComponentTransition(stage_id=stage_id, task_id=task_id, instance_id=instance_id,
                                   candidate_id=candidate_id,
                                   iteration=iteration, status="unknown",
                                   before_checkpoint=before_checkpoint, after_checkpoint=after_checkpoint,
                                   before_correct=before_correct, after_correct=after_correct)
    improved = int(not before_correct and after_correct)
    regressed = int(before_correct and not after_correct)
    return ComponentTransition(stage_id=stage_id, task_id=task_id, instance_id=instance_id,
                               candidate_id=candidate_id,
                               iteration=iteration, before_checkpoint=before_checkpoint,
                               after_checkpoint=after_checkpoint, status="observed", eligible_pairs=1,
                               wrong_to_right=improved,
                               right_to_wrong=regressed, net_ex_change=float(improved - regressed),
                               before_correct=before_correct, after_correct=after_correct)


class ComponentTransitionSummary(StrictModel):
    protocol_id: str = "project.component-transition-summary@2"
    eligible_pairs: int
    wrong_before_count: int
    correct_before_count: int
    wrong_to_right: int
    right_to_wrong: int
    fix_rate_given_wrong: float | None
    degradation_rate_given_correct: float | None
    normalized_net_ex_change: float | None
    unknown_pairs: int


def aggregate_component_transitions(items: list[ComponentTransition]) -> ComponentTransitionSummary:
    observed = [item for item in items if item.status == "observed"]
    improved = sum(item.wrong_to_right or 0 for item in observed)
    regressed = sum(item.right_to_wrong or 0 for item in observed)
    wrong_before = sum(item.before_correct is False for item in observed)
    correct_before = len(observed) - wrong_before
    return ComponentTransitionSummary(
        eligible_pairs=len(observed), wrong_before_count=wrong_before,
        correct_before_count=correct_before, wrong_to_right=improved, right_to_wrong=regressed,
        fix_rate_given_wrong=improved / wrong_before if wrong_before else None,
        degradation_rate_given_correct=regressed / correct_before if correct_before else None,
        normalized_net_ex_change=(improved - regressed) / len(observed) if observed else None,
        unknown_pairs=len(items) - len(observed),
    )


class SelectionObservation(StrictModel):
    protocol_id: str = "project.selection-observation@2"
    stage_id: str
    task_id: str
    instance_id: str
    iteration: int = Field(ge=0)
    candidate_ids: list[str]
    selected_candidate_id: str
    input_oracle: float | None
    selected_correct: float | None
    regret: float | None
    missed_correct: int | None
    unknown_candidate_outcomes: int
    input_candidate_count: int
    known_candidate_count: int
    oracle_denominator: int
    selected_denominator: int
    regret_denominator: int
    missed_correct_denominator: int


def evaluate_selection(candidate_outcomes: dict[str, bool | None], selected_candidate_id: str, *,
                       stage_id: str, task_id: str, instance_id: str,
                       iteration: int) -> SelectionObservation:
    if selected_candidate_id not in candidate_outcomes:
        raise ValueError("selected candidate must be in the observed input pool")
    known = [value for value in candidate_outcomes.values() if value is not None]
    oracle = float(any(known)) if known else None
    selected = candidate_outcomes[selected_candidate_id]
    selected_value = float(selected) if selected is not None else None
    regret = oracle - selected_value if oracle is not None and selected_value is not None else None
    missed = int(oracle == 1 and selected_value == 0) if regret is not None else None
    return SelectionObservation(stage_id=stage_id, task_id=task_id, instance_id=instance_id,
                                iteration=iteration, candidate_ids=list(candidate_outcomes),
                                selected_candidate_id=selected_candidate_id,
                                input_oracle=oracle, selected_correct=selected_value, regret=regret,
                                missed_correct=missed,
                                unknown_candidate_outcomes=sum(v is None for v in candidate_outcomes.values()),
                                input_candidate_count=len(candidate_outcomes), known_candidate_count=len(known),
                                oracle_denominator=int(bool(known)), selected_denominator=int(selected is not None),
                                regret_denominator=int(regret is not None),
                                missed_correct_denominator=int(missed is not None))


class ScalingObservation(StrictModel):
    protocol_id: str = "project.scaling-observation@2"
    stage_id: str
    task_id: str
    instance_id: str
    iteration: int = Field(ge=0)
    input_candidate_ids: list[str]
    output_candidate_ids: list[str]
    correct_input_count: int
    surviving_correct_count: int
    gained_correct_count: int
    input_correct_denominator: int
    output_correct_denominator: int
    textual_diversity_denominator: int
    structural_diversity_denominator: int
    textual_diversity: float | None
    structural_diversity: float | None


class DecompositionObservation(StrictModel):
    protocol_id: str = "project.decomposition-observation@2"
    stage_id: str
    task_id: str
    instance_id: str
    iteration: int = Field(ge=0)
    triggered: bool
    plan_ref: str | None
    runtime_task_ids: list[str]
    interpretation: str = "observed_trigger_only"


def evaluate_scaling(input_sql: dict[str, str], output_sql: dict[str, str],
                     input_correct: dict[str, bool | None], output_correct: dict[str, bool | None],
                     structural_keys: dict[str, str] | None = None, *,
                     stage_id: str, task_id: str, instance_id: str,
                     iteration: int) -> ScalingObservation:
    if not set(input_correct).issubset(input_sql):
        raise ValueError("input correctness references a candidate outside the input pool")
    if not set(output_correct).issubset(output_sql):
        raise ValueError("output correctness references a candidate outside the output pool")
    input_correct_ids = {key for key, value in input_correct.items() if value is True}
    output_correct_ids = {key for key, value in output_correct.items() if value is True}
    output_values = list(output_sql.values())
    structural_values = [structural_keys[key] for key in output_sql
                         if structural_keys is not None and key in structural_keys]
    return ScalingObservation(
        stage_id=stage_id, task_id=task_id, instance_id=instance_id, iteration=iteration,
        input_candidate_ids=list(input_sql), output_candidate_ids=list(output_sql),
        correct_input_count=len(input_correct_ids),
        surviving_correct_count=len(input_correct_ids & output_correct_ids),
        gained_correct_count=len(output_correct_ids - input_correct_ids),
        input_correct_denominator=sum(value is not None for value in input_correct.values()),
        output_correct_denominator=sum(value is not None for value in output_correct.values()),
        textual_diversity_denominator=len(output_values),
        structural_diversity_denominator=len(structural_values),
        textual_diversity=len(set(output_values)) / len(output_values) if output_values else None,
        structural_diversity=(len(set(structural_values)) / len(structural_values)
                              if structural_values else None),
    )


def evaluate_decomposition(*, triggered: bool, plan_ref: str | None,
                           runtime_task_ids: list[str], stage_id: str, task_id: str,
                           instance_id: str, iteration: int) -> DecompositionObservation:
    """Record the observed trigger and spawned tasks without inferring benefit."""
    return DecompositionObservation(
        stage_id=stage_id, task_id=task_id, instance_id=instance_id, iteration=iteration,
        triggered=triggered, plan_ref=plan_ref, runtime_task_ids=runtime_task_ids,
    )
