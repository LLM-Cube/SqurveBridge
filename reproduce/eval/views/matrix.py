"""L6 cross-method comparative metrics over the eval store.

Derived from the correctness/latency matrix ``(query, method) -> (Y, t)``.
Definitions follow arXiv 2602.15564 Section 3 and Appendices B/C:

- ``EX_static  = max_i mean_q Y_i(q)``
- ``EX_dynamic = mean_q max_i Y_i(q)`` (oracle per-query selection)
- ``oracle_gap = EX_dynamic - EX_static``
- ``D_sample(i,j) = mean_q 1{Y_i(q) != Y_j(q)}``
- ``D_eff(i,j)    = mean_q |t_i - t_j| / (t_i + t_j)``
- ``N(q)`` = number of methods solving q (empirical difficulty)
- ``efficiency_headroom(N) = (mean t_max - mean t_min) / mean t_max`` over the
  correct-method latencies of queries in difficulty stratum N.

Everything is deterministic dictionary math over recorded runs; no LLM calls.
"""

from __future__ import annotations

from collections import defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Dict, List, Optional

from reproduce.eval.views.store import load_sample_metric


class MethodMatrix:
    """Correctness and latency per (method, query), on the common query set."""

    def __init__(self, correctness: Dict[str, Dict[str, float]],
                 latency: Dict[str, Dict[str, float]] | None = None) -> None:
        self.methods = sorted(correctness)
        common: set[str] | None = None
        for method in self.methods:
            ids = set(correctness[method])
            common = ids if common is None else common & ids
        self.query_ids = sorted(common or set())
        self.correct = {
            method: {qid: int(bool(correctness[method][qid])) for qid in self.query_ids}
            for method in self.methods
        }
        latency = latency or {}
        self.latency = {
            method: {
                qid: float(latency[method][qid])
                for qid in self.query_ids
                if isinstance((latency.get(method) or {}).get(qid), (int, float))
            }
            for method in self.methods
        }
        self.cohort: Dict[str, Any] | None = None

    @classmethod
    def from_store(
            cls,
            db_path: str | Path,
            *,
            dataset: Optional[str] = None,
            split: Optional[str] = None,
    ) -> "MethodMatrix":
        correctness = load_sample_metric(db_path, "ex", dataset=dataset, split=split)
        latency = load_sample_metric(db_path, "act_elapsed_s", dataset=dataset, split=split)
        return cls(correctness, latency)

    @classmethod
    def from_v2_runs(cls, db_path: str | Path, run_ids: List[str], *,
                     allowed_differences: set[str] | None = None) -> "MethodMatrix":
        """Build from explicit runs after validating their complete cohort descriptors."""
        import json
        import sqlite3

        from reproduce.eval.protocol import CohortDescriptor, compare_bundles
        from reproduce.eval.views.store import load_v2_run_metric

        if not run_ids:
            raise ValueError("at least one explicit run_id is required")
        declared_differences = set(allowed_differences or set())
        descriptors = []
        methods = []
        selected_metric_protocols: list[tuple[str, str]] = []
        with closing(sqlite3.connect(db_path)) as conn:
            for run_id in run_ids:
                row = conn.execute(
                    "SELECT r.method, r.dataset, r.split, c.database_snapshot, c.candidate_protocol, "
                    "c.evaluator_protocol, c.model_config_id, c.sample_ids_json "
                    "FROM runs r JOIN run_cohorts_v2 c ON c.run_id=r.run_id WHERE r.run_id=?", (run_id,)
                ).fetchone()
                if row is None:
                    raise ValueError(f"missing v2 cohort for run: {run_id}")
                method, dataset, split, snapshot, candidate_protocol, evaluator_protocol, model, ids = row
                if method in methods:
                    raise ValueError(f"duplicate method in cohort: {method}")
                methods.append(method)
                descriptors.append(CohortDescriptor(
                    dataset=dataset, split=split, database_snapshot=snapshot,
                    candidate_protocol=candidate_protocol, evaluator_protocol=evaluator_protocol,
                    model_config=model, sample_ids=json.loads(ids),
                ))
                identities = conn.execute(
                    "SELECT DISTINCT protocol_id, protocol_version FROM metric_results_v2 "
                    "WHERE run_id=? AND metric_id='selected_ex'", (run_id,),
                ).fetchall()
                if len(identities) != 1:
                    raise ValueError(f"selected_ex protocol identity is incomplete in run: {run_id}")
                selected_metric_protocols.append((str(identities[0][0]), str(identities[0][1])))
        base = descriptors[0]
        comparisons = [compare_bundles(base, item, allowed_differences=declared_differences)
                       for item in descriptors[1:]]
        failures = [reason for comparison in comparisons for reason in comparison.reasons]
        if failures:
            raise ValueError("incompatible v2 cohort: " + ",".join(sorted(set(failures))))
        if ("evaluator_protocol" not in declared_differences
                and len(set(selected_metric_protocols)) != 1):
            raise ValueError("incompatible v2 cohort: selected_ex_protocol_mismatch")
        with closing(sqlite3.connect(db_path)) as conn:
            for run_id in run_ids:
                rows = conn.execute(
                    "SELECT a.instance_id, a.candidate_ids_json, b.candidate_ids_json "
                    "FROM metric_results_v2 a JOIN metric_results_v2 b "
                    "ON a.run_id=b.run_id AND a.instance_id=b.instance_id "
                    "WHERE a.run_id=? AND a.metric_id='selected_ex' "
                    "AND b.metric_id='sql_execution_time'", (run_id,)
                ).fetchall()
                if any(correct_ids != latency_ids for _instance, correct_ids, latency_ids in rows):
                    raise ValueError(f"candidate evidence mismatch in run: {run_id}")
        correctness = {method: load_v2_run_metric(db_path, run_id, "selected_ex")
                       for method, run_id in zip(methods, run_ids)}
        latency = {method: load_v2_run_metric(db_path, run_id, "sql_execution_time")
                   for method, run_id in zip(methods, run_ids)}
        matrix = cls(correctness, latency)
        union = set().union(*(set(item.sample_ids) for item in descriptors))
        matrix.cohort = {
            "run_ids": run_ids, "common_set_size": len(matrix.query_ids),
            "excluded_sample_count": len(union - set(matrix.query_ids)),
            "latency_coverage": {
                method: len(matrix.latency[method]) / len(matrix.query_ids) if matrix.query_ids else 0
                for method in matrix.methods
            },
            "empirical_upper_bound_scope": "declared_cohort_and_evaluated_methods",
            "correctness_metric": "selected_ex",
            "latency_metric": "sql_execution_time",
            "allowed_differences": sorted(declared_differences),
            "selected_ex_protocols": {
                method: {"protocol_id": identity[0], "protocol_version": identity[1]}
                for method, identity in zip(methods, selected_metric_protocols)
            },
        }
        return matrix


def oracle_gap(matrix: MethodMatrix) -> Dict[str, Any]:
    if not matrix.methods or not matrix.query_ids:
        return {"ex_static": None, "ex_dynamic": None, "gap": None, "best_static_method": None}
    per_method = {
        method: sum(matrix.correct[method].values()) / len(matrix.query_ids)
        for method in matrix.methods
    }
    best_method = max(per_method, key=per_method.get)
    dynamic = sum(
        max(matrix.correct[method][qid] for method in matrix.methods)
        for qid in matrix.query_ids
    ) / len(matrix.query_ids)
    return {
        "ex_static": per_method[best_method],
        "ex_dynamic": dynamic,
        "gap": dynamic - per_method[best_method],
        "best_static_method": best_method,
        "per_method_ex": per_method,
        "query_count": len(matrix.query_ids),
    }


def disagreement(matrix: MethodMatrix) -> Dict[str, Dict[str, Dict[str, float]]]:
    result: Dict[str, Dict[str, Dict[str, float]]] = {}
    for i, method_a in enumerate(matrix.methods):
        for method_b in matrix.methods[i + 1:]:
            sample_terms = [
                1 if matrix.correct[method_a][qid] != matrix.correct[method_b][qid] else 0
                for qid in matrix.query_ids
            ]
            efficiency_terms = []
            for qid in matrix.query_ids:
                t_a = matrix.latency.get(method_a, {}).get(qid)
                t_b = matrix.latency.get(method_b, {}).get(qid)
                if t_a is not None and t_b is not None and (t_a + t_b) > 0:
                    efficiency_terms.append(abs(t_a - t_b) / (t_a + t_b))
            d_sample = sum(sample_terms) / len(sample_terms) if sample_terms else None
            d_eff = sum(efficiency_terms) / len(efficiency_terms) if efficiency_terms else None
            combined = None
            if d_sample is not None and d_eff is not None:
                combined = (d_sample + d_eff) / 2
            result.setdefault(method_a, {})[method_b] = {
                "sample": d_sample, "efficiency": d_eff, "combined": combined,
            }
    return result


def empirical_difficulty(matrix: MethodMatrix) -> Dict[str, Any]:
    n_by_query = {
        qid: sum(matrix.correct[method][qid] for method in matrix.methods)
        for qid in matrix.query_ids
    }
    histogram: Dict[int, int] = defaultdict(int)
    for count in n_by_query.values():
        histogram[count] += 1
    return {"per_query": n_by_query, "histogram": dict(sorted(histogram.items()))}


def uniquely_solved(matrix: MethodMatrix) -> Dict[str, Any]:
    counts = {method: 0 for method in matrix.methods}
    queries: Dict[str, List[str]] = {method: [] for method in matrix.methods}
    for qid in matrix.query_ids:
        solvers = [method for method in matrix.methods if matrix.correct[method][qid]]
        if len(solvers) == 1:
            counts[solvers[0]] += 1
            queries[solvers[0]].append(qid)
    return {"counts": counts, "queries": queries}


def efficiency_headroom(matrix: MethodMatrix) -> Dict[int, Dict[str, float]]:
    """Correctness-constrained latency headroom per difficulty stratum N(q)."""
    strata: Dict[int, List[tuple[float, float]]] = defaultdict(list)
    for qid in matrix.query_ids:
        correct_latencies = [
            matrix.latency[method][qid]
            for method in matrix.methods
            if matrix.correct[method][qid] and qid in matrix.latency.get(method, {})
        ]
        if not correct_latencies:
            continue
        strata[sum(matrix.correct[method][qid] for method in matrix.methods)].append(
            (max(correct_latencies), min(correct_latencies)))
    result = {}
    for stratum, pairs in sorted(strata.items()):
        mean_max = sum(pair[0] for pair in pairs) / len(pairs)
        mean_min = sum(pair[1] for pair in pairs) / len(pairs)
        result[stratum] = {
            "query_count": len(pairs),
            "mean_t_max": mean_max,
            "mean_t_min": mean_min,
            "headroom": (mean_max - mean_min) / mean_max if mean_max else None,
        }
    return result


def matrix_report(matrix: MethodMatrix) -> Dict[str, Any]:
    """All L6 metrics in one payload (registry ids as keys)."""
    return {
        "methods": matrix.methods,
        "query_count": len(matrix.query_ids),
        "oracle_gap": oracle_gap(matrix),
        "method_disagreement": disagreement(matrix),
        "empirical_difficulty": empirical_difficulty(matrix)["histogram"],
        "uniquely_solved": uniquely_solved(matrix)["counts"],
        "efficiency_headroom": efficiency_headroom(matrix),
        "cohort": matrix.cohort,
    }
