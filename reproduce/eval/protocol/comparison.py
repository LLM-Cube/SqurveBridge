from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from .models import ComparisonPolicy


@dataclass(frozen=True)
class ComparisonOutcome:
    equal: bool
    column_mapping: tuple[int, ...] | None = None
    reason: str | None = None


class _ColumnMappingSearchLimit(RuntimeError):
    pass


def _equal_value(left: Any, right: Any, policy: ComparisonPolicy) -> bool:
    if left is None or right is None:
        return policy.nulls_equal and left is None and right is None
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        if not (math.isfinite(float(left)) and math.isfinite(float(right))):
            return left == right
        return math.isclose(float(left), float(right), abs_tol=policy.absolute_tolerance,
                            rel_tol=policy.relative_tolerance)
    return left == right


def _rows_equal(left: Sequence[Any], right: Sequence[Any], policy: ComparisonPolicy) -> bool:
    return len(left) == len(right) and all(_equal_value(a, b, policy) for a, b in zip(left, right))


def _sequence_equal(pred: list[tuple[Any, ...]], gold: list[tuple[Any, ...]], policy: ComparisonPolicy) -> bool:
    if policy.row_order == "preserve":
        return len(pred) == len(gold) and all(_rows_equal(a, b, policy) for a, b in zip(pred, gold))
    unmatched = list(gold)
    for row in pred:
        for index, candidate in enumerate(unmatched):
            if _rows_equal(row, candidate, policy):
                unmatched.pop(index)
                break
        else:
            return False
    return not unmatched


def compare_results(predicted: Sequence[Sequence[Any]], gold: Sequence[Sequence[Any]],
                    policy: ComparisonPolicy, *, predicted_columns: Sequence[str] | None = None,
                    gold_columns: Sequence[str] | None = None) -> ComparisonOutcome:
    pred = [tuple(row) for row in predicted]
    ref = [tuple(row) for row in gold]
    pred_width = len(predicted_columns) if predicted_columns is not None else (
        len(pred[0]) if pred else (len(ref[0]) if ref else 0))
    gold_width = len(gold_columns) if gold_columns is not None else (
        len(ref[0]) if ref else pred_width)
    if any(len(row) != pred_width for row in pred) or any(len(row) != gold_width for row in ref):
        return ComparisonOutcome(False, reason="ragged_rows")
    if pred_width < gold_width or (pred_width != gold_width and policy.extra_columns == "reject"):
        return ComparisonOutcome(False, reason="column_count_mismatch")
    if not pred and not ref:
        return ComparisonOutcome(policy.empty_results_equal, tuple(range(gold_width)))
    mappings: Sequence[tuple[int, ...]] | Any = [tuple(range(gold_width))]
    if policy.column_order == "map_one_to_one":
        mappings = _candidate_mappings(pred, ref, pred_width, gold_width, policy)
    try:
        for mapping in mappings:
            projected = [tuple(row[index] for index in mapping) for row in pred]
            if _sequence_equal(projected, ref, policy):
                return ComparisonOutcome(True, mapping)
    except _ColumnMappingSearchLimit:
        return ComparisonOutcome(False, reason="column_mapping_search_limit")
    return ComparisonOutcome(False, reason="row_or_value_mismatch")


def _candidate_mappings(pred: list[tuple[Any, ...]], gold: list[tuple[Any, ...]],
                        pred_width: int, gold_width: int, policy: ComparisonPolicy):
    edges: list[list[int]] = []
    column_policy = policy.model_copy(update={"column_order": "preserve", "extra_columns": "reject"})
    for gold_index in range(gold_width):
        gold_column = [(row[gold_index],) for row in gold]
        compatible = []
        for pred_index in range(pred_width):
            pred_column = [(row[pred_index],) for row in pred]
            if _sequence_equal(pred_column, gold_column, column_policy):
                compatible.append(pred_index)
        edges.append(compatible)
    order = sorted(range(gold_width), key=lambda index: len(edges[index]))
    mapping = [-1] * gold_width
    visited_nodes = 0

    def visit(position: int, used: set[int]):
        nonlocal visited_nodes
        visited_nodes += 1
        if visited_nodes > policy.max_column_mappings:
            raise _ColumnMappingSearchLimit
        if position == len(order):
            yield tuple(mapping)
            return
        gold_index = order[position]
        for pred_index in edges[gold_index]:
            if pred_index in used:
                continue
            mapping[gold_index] = pred_index
            yield from visit(position + 1, used | {pred_index})

    return visit(0, set())
