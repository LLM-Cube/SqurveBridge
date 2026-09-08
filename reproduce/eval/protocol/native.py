from __future__ import annotations

from collections.abc import Callable
import hashlib
import importlib.util
from pathlib import Path
import sys
from typing import Any

from pydantic import Field

from .models import MetricResult, StrictModel


class NativeAdapterSpec(StrictModel):
    adapter_id: str
    benchmark: str
    dataset: str
    split: str
    upstream_revision: str
    options: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[str] = Field(default_factory=list)
    license_status: str
    resource_checksums: dict[str, str] = Field(default_factory=dict)
    test_suite: bool = False


class NativeEvaluatorRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, tuple[NativeAdapterSpec, Callable[[dict[str, Any]], float]]] = {}

    def register(self, spec: NativeAdapterSpec, evaluator: Callable[[dict[str, Any]], float]) -> None:
        if spec.adapter_id in self._entries:
            raise ValueError(f"duplicate native adapter: {spec.adapter_id}")
        self._entries[spec.adapter_id] = (spec, evaluator)

    def evaluate(self, adapter_id: str, payload: dict[str, Any], *, enabled: bool,
                 available_resources: dict[str, str]) -> MetricResult:
        if adapter_id not in self._entries:
            return _unavailable(adapter_id, "unsupported_adapter")
        spec, evaluator = self._entries[adapter_id]
        provenance = spec.model_dump()
        if not enabled:
            return _unavailable(adapter_id, "adapter_disabled", provenance, spec.upstream_revision)
        verified_paths: dict[str, Path] = {}
        for name, checksum in spec.resource_checksums.items():
            if name not in available_resources:
                return _unavailable(adapter_id, f"resource_missing:{name}", provenance, spec.upstream_revision)
            resource_path = Path(available_resources[name])
            if not resource_path.is_file():
                return _unavailable(adapter_id, f"resource_missing:{name}", provenance, spec.upstream_revision)
            if _sha256_file(resource_path) != checksum:
                return _unavailable(adapter_id, f"resource_checksum_mismatch:{name}", provenance, spec.upstream_revision)
            verified_paths[name] = resource_path.resolve()
        dataset = payload.get("dataset")
        split = payload.get("split")
        if dataset is None:
            return _unavailable(adapter_id, "applicability_unavailable:dataset", provenance,
                                spec.upstream_revision)
        if str(dataset).lower() != spec.dataset.lower():
            return _unavailable(adapter_id, "dataset_mismatch", provenance, spec.upstream_revision)
        if split is None:
            return _unavailable(adapter_id, "applicability_unavailable:split", provenance,
                                spec.upstream_revision)
        if str(split).lower() != spec.split.lower():
            return _unavailable(adapter_id, "split_mismatch", provenance, spec.upstream_revision)
        if spec.test_suite:
            database_path = Path(str(payload.get("database_path", ""))).resolve()
            if database_path not in verified_paths.values():
                return _unavailable(adapter_id, "database_not_in_verified_suite", provenance,
                                    spec.upstream_revision)
            actual_suite = {path.resolve() for path in database_path.parent.glob("*.sqlite")}
            if actual_suite != set(verified_paths.values()):
                return _unavailable(adapter_id, "suite_manifest_mismatch", provenance,
                                    spec.upstream_revision)
        value = evaluator(payload)
        return MetricResult(metric_id=f"native.{adapter_id}", protocol_id=f"native.{adapter_id}",
                            protocol_version=spec.upstream_revision, status="available", value=value,
                            numerator=int(value == 1), denominator=1, coverage=1,
                            value_domain="unit_interval", provenance=provenance)


SPIDER_TEST_SUITE_REVISION = "e97acc546ecbee8fa27fa8dbf025ef61493a876c"
_VENDORED_CHECKSUMS = {
    "exec_eval.py": "29d034db28904490c28037537a14fbb0150b6e86cef0049076c0511d6b6b77f7",
    "parse.py": "ef04211a6e1c1e142571157f5c1999613e3451084c044083b2de1977f1f622c5",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def register_builtin_spider_test_suite(
        registry: NativeEvaluatorRegistry, *, resource_checksums: dict[str, str], split: str = "dev",
) -> None:
    """Register the verified vendored upstream implementation at its fixed revision."""
    registry.register(NativeAdapterSpec(
        adapter_id="spider_test_suite", benchmark="spider", dataset="spider", split=split,
        upstream_revision=SPIDER_TEST_SUITE_REVISION, dependencies=["vendored:test-suite-sql-eval"],
        license_status="Apache-2.0", resource_checksums=resource_checksums, test_suite=True,
    ), _evaluate_vendored_spider_test_suite)


def _evaluate_vendored_spider_test_suite(payload: dict[str, Any]) -> float:
    vendor = Path(__file__).resolve().parents[3] / "third_party" / "test_suite_sql_eval"
    for name, expected in _VENDORED_CHECKSUMS.items():
        actual = _sha256_file(vendor / name)
        if actual != expected:
            raise RuntimeError(f"vendored evaluator checksum mismatch: {name}")
    module_path = vendor / "exec_eval.py"
    module_name = "squrvebridge_vendored_test_suite_exec_eval"
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load vendored test-suite evaluator")
    parse_spec = importlib.util.spec_from_file_location("parse", vendor / "parse.py")
    if parse_spec is None or parse_spec.loader is None:
        raise RuntimeError("cannot load vendored test-suite parser")
    prior_parse = sys.modules.get("parse")
    sys.path.insert(0, str(vendor))
    try:
        parse_module = importlib.util.module_from_spec(parse_spec)
        sys.modules["parse"] = parse_module
        parse_spec.loader.exec_module(parse_module)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # The pinned upstream entry point enumerates and evaluates every ``*.sqlite``
        # file beside database_path.  The registry gate above binds that exact set
        # to the verified manifest before this call.
        value = module.eval_exec_match(
            str(payload["database_path"]), str(payload["predicted_sql"]), str(payload["gold_sql"]),
            bool(payload.get("plug_value", False)), bool(payload.get("keep_distinct", False)), False,
        )
    finally:
        sys.path.pop(0)
        if prior_parse is None:
            sys.modules.pop("parse", None)
        else:
            sys.modules["parse"] = prior_parse
    return float(value)


def _unavailable(adapter_id: str, reason: str, provenance: dict[str, Any] | None = None,
                 protocol_version: str = "unresolved") -> MetricResult:
    return MetricResult(metric_id=f"native.{adapter_id}", protocol_id=f"native.{adapter_id}",
                        protocol_version=protocol_version, status="unavailable", coverage=0,
                        value_domain="unit_interval", reason=reason, provenance=provenance or {})
