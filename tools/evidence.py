#!/usr/bin/env python3
"""Export and verify privacy-safe, checksummed SqurveBridge evidence bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


SCHEMA_VERSION = "1.0"
OUTPUT_FILES = (
    "config.json",
    "scores.json",
    "report.md",
    "sample-diagnostics.jsonl",
)
CHECKSUM_FILE = "checksums.sha256"
MANIFEST_FILE = "manifest.json"
ALLOWED_BUNDLE_FILES = frozenset((*OUTPUT_FILES, CHECKSUM_FILE, MANIFEST_FILE))

REQUIRED_METADATA = frozenset(
    {
        "method",
        "benchmark",
        "split",
        "sample_count",
        "provider",
        "model",
        "code_commit",
        "evaluator_version",
        "source_alignment",
    }
)
ALLOWED_METADATA = REQUIRED_METADATA | {"notes"}
ALLOWED_SOURCE_ALIGNMENT = {"source-aligned", "cross-benchmark", "demo"}
ALLOWED_DIAGNOSTIC_FIELDS = frozenset(
    {
        "instance_id",
        "metrics",
        "error_category",
        "error_categories",
        "stage_status",
        "token_usage",
        "latency_ms",
        "metric_availability",
    }
)
FORBIDDEN_KEY_PARTS = {
    "question",
    "utterance",
    "prompt",
    "db_row",
    "database_row",
    "row_data",
    "gold_sql",
    "pred_sql",
    "prediction_sql",
    "raw_request",
    "raw_response",
    "provider_request",
    "provider_response",
    "request_body",
    "response_body",
    "api_key",
    "access_token",
    "secret_key",
    "authorization",
}
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[opusr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|access[_-]?token|secret[_-]?key)\s*[:=]\s*['\"]?(?!\$\{|your[_-]|<|placeholder|null\b|none\b)[A-Za-z0-9_./+=-]{12,}"),
)
ABSOLUTE_PATH_PATTERNS = (
    re.compile(r"(?<![A-Za-z0-9_])/(?:Users|home|root|private|var|tmp|opt|etc)/[^\s'\"`]+"),
    re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:\\[^\s'\"`]+"),
)
SQL_TEXT_PATTERN = re.compile(
    r"(?is)\b(?:select\s+.{1,200}?\s+from|insert\s+into|update\s+\S+\s+set|delete\s+from)\b"
)
PLACEHOLDER_PATTERN = re.compile(
    r"(?i)^(?:|none|null|placeholder|your[_ -].*|<[^>]+>|\$\{ENV:[A-Z][A-Z0-9_]*\})$"
)


class EvidenceError(ValueError):
    """Raised when an evidence bundle violates the public contract."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvidenceError(f"invalid JSON file: {path}") from exc


def _safe_text(text: str, *, context: str) -> None:
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            raise EvidenceError(f"{context}: possible secret detected")
    for pattern in ABSOLUTE_PATH_PATTERNS:
        if pattern.search(text):
            raise EvidenceError(f"{context}: absolute local path detected")
    if SQL_TEXT_PATTERN.search(text):
        raise EvidenceError(f"{context}: SQL text detected")


def _normalise_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def _check_structure(value: Any, *, context: str = "document") -> None:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            if not isinstance(raw_key, str):
                raise EvidenceError(f"{context}: object keys must be strings")
            key = _normalise_key(raw_key)
            token_counter = context.endswith(".token_usage") and isinstance(
                child, (int, float)
            ) and not isinstance(child, bool)
            negative_privacy_declaration = context == "manifest.privacy" and child is False
            if (
                any(part in key for part in FORBIDDEN_KEY_PARTS)
                and not token_counter
                and not negative_privacy_declaration
            ):
                raise EvidenceError(f"{context}: forbidden field {raw_key!r}")
            _safe_text(raw_key, context=context)
            _check_structure(child, context=f"{context}.{raw_key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _check_structure(child, context=f"{context}[{index}]")
    elif isinstance(value, str):
        _safe_text(value, context=context)
    elif isinstance(value, float) and not math.isfinite(value):
        raise EvidenceError(f"{context}: non-finite numbers are not allowed")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise EvidenceError(f"{context}: unsupported value type")


def _validate_metadata(metadata: Any) -> dict[str, Any]:
    if not isinstance(metadata, dict):
        raise EvidenceError("metadata must be a JSON object")
    unknown = set(metadata) - ALLOWED_METADATA
    missing = REQUIRED_METADATA - set(metadata)
    if unknown or missing:
        raise EvidenceError(
            f"metadata fields invalid; missing={sorted(missing)}, unknown={sorted(unknown)}"
        )
    if metadata["source_alignment"] not in ALLOWED_SOURCE_ALIGNMENT:
        raise EvidenceError("metadata.source_alignment is invalid")
    if (
        not isinstance(metadata["sample_count"], int)
        or isinstance(metadata["sample_count"], bool)
        or metadata["sample_count"] < 0
    ):
        raise EvidenceError("metadata.sample_count must be a non-negative integer")
    for key in REQUIRED_METADATA - {"sample_count"}:
        if not isinstance(metadata[key], str) or not metadata[key].strip():
            raise EvidenceError(f"metadata.{key} must be a non-empty string")
    _check_structure(metadata, context="metadata")
    return metadata


def _credential_placeholders_only(value: Any) -> bool:
    if isinstance(value, dict):
        return all(_credential_placeholders_only(child) for child in value.values())
    if isinstance(value, list):
        return all(_credential_placeholders_only(child) for child in value)
    if value is None:
        return True
    return isinstance(value, str) and bool(PLACEHOLDER_PATTERN.fullmatch(value.strip()))


def _sanitise_config(
    config: Any, *, strip_credentials: bool = True
) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise EvidenceError("config must be a JSON object")
    credential_keys = [key for key in config if _normalise_key(key) == "api_key"]
    if not strip_credentials and credential_keys:
        raise EvidenceError("config: credential fields are not allowed in a public bundle")
    for key in credential_keys:
        if not _credential_placeholders_only(config[key]):
            raise EvidenceError("config: non-placeholder credential detected")
    # Credential settings are never part of a public snapshot, even placeholders.
    clean = {key: value for key, value in config.items() if _normalise_key(key) != "api_key"}
    _check_structure(clean, context="config")
    return clean


def _sanitise_scores(
    scores: Any, *, strip_sample_records: bool = True
) -> dict[str, Any]:
    if not isinstance(scores, dict):
        raise EvidenceError("scores must be a JSON object")
    record_keys = {"per_sample", "samples", "records", "predictions", "examples"}

    def sanitise(value: Any, *, context: str) -> Any:
        if isinstance(value, dict):
            clean: dict[str, Any] = {}
            for raw_key, child in value.items():
                if not isinstance(raw_key, str):
                    raise EvidenceError(f"{context}: object keys must be strings")
                key = _normalise_key(raw_key)
                is_sample_record = key in record_keys
                is_local_path = key == "path" or key.endswith("_path")
                if is_sample_record or is_local_path:
                    if not strip_sample_records:
                        kind = "sample-level records" if is_sample_record else "local path fields"
                        raise EvidenceError(f"{context}: {kind} are not allowed")
                    continue
                clean[raw_key] = sanitise(child, context=f"{context}.{raw_key}")
            return clean
        if isinstance(value, list):
            return [
                sanitise(child, context=f"{context}[{index}]")
                for index, child in enumerate(value)
            ]
        return value

    clean = sanitise(scores, context="scores")
    _check_structure(clean, context="scores")
    return clean


def _read_diagnostics(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise EvidenceError(f"cannot read diagnostics: {path}") from exc
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"diagnostics line {line_number}: invalid JSON") from exc
        if not isinstance(record, dict):
            raise EvidenceError(f"diagnostics line {line_number}: expected object")
        unknown = set(record) - ALLOWED_DIAGNOSTIC_FIELDS
        if unknown:
            raise EvidenceError(
                f"diagnostics line {line_number}: forbidden or unknown fields {sorted(unknown)}"
            )
        if "instance_id" not in record or not isinstance(record["instance_id"], (str, int)):
            raise EvidenceError(f"diagnostics line {line_number}: instance_id is required")
        _check_structure(record, context=f"diagnostics[{line_number}]")
        records.append(record)
    return records


def _require_publication_completeness(scores: Any) -> None:
    """Refuse to export a bundle that silently drops declared-public metrics.

    The metric registry declares which aggregates a bundle must carry; a
    metric only counts as missing when its raw signal was captured (so legacy
    aggregate-only inputs still export cleanly).
    """
    if not isinstance(scores, dict):
        return
    aggregate = scores.get("aggregate")
    if not isinstance(aggregate, dict) or not (aggregate.keys() & {"ex", "em", "sf1", "ves", "rves"}):
        # Not a runner score bundle (freeform/legacy payload); the sanitizer
        # still applies, but there is no declared-metric contract to enforce.
        return
    from reproduce.eval.views.evidence import publication_completeness

    missing = publication_completeness(scores)
    if missing:
        raise EvidenceError(
            "scores: declared publishable metrics missing from aggregate: "
            + ", ".join(sorted(missing))
        )


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _diagnostics_bytes(records: Iterable[dict[str, Any]]) -> bytes:
    return "".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for record in records
    ).encode("utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def export_bundle(args: argparse.Namespace) -> Path:
    run_id = args.run_id.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise EvidenceError("run-id must be a safe portable directory name")
    metadata = _validate_metadata(_read_json(args.metadata))
    config = _sanitise_config(_read_json(args.config))
    raw_scores = _read_json(args.scores)
    _require_publication_completeness(raw_scores)
    scores = _sanitise_scores(raw_scores)
    diagnostics = _read_diagnostics(args.diagnostics)
    try:
        report = args.report.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise EvidenceError(f"cannot read report: {args.report}") from exc
    _safe_text(report, context="report")

    output_root = args.output_root.resolve()
    destination = output_root / run_id
    if destination.exists():
        raise EvidenceError(f"destination already exists: {destination}")
    output_root.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{run_id}-", dir=output_root))
    try:
        (temp_dir / "config.json").write_bytes(_json_bytes(config))
        (temp_dir / "scores.json").write_bytes(_json_bytes(scores))
        (temp_dir / "report.md").write_text(report.rstrip() + "\n", encoding="utf-8")
        (temp_dir / "sample-diagnostics.jsonl").write_bytes(_diagnostics_bytes(diagnostics))

        checksums = {name: _sha256(temp_dir / name) for name in OUTPUT_FILES}
        manifest = {
            "schema_version": SCHEMA_VERSION,
            "run_id": run_id,
            "privacy": {
                "classification": "public-sanitized",
                "contains_questions": False,
                "contains_database_rows": False,
                "contains_sql_text": False,
                "contains_provider_payloads": False,
                "contains_credentials": False,
                "contains_absolute_paths": False,
            },
            "provenance": metadata,
            "exporter": {"name": "SqurveBridge evidence exporter", "code_commit": _git_commit()},
            "files": checksums,
        }
        (temp_dir / MANIFEST_FILE).write_bytes(_json_bytes(manifest))
        checksum_lines = [
            f"{_sha256(temp_dir / name)}  {name}\n"
            for name in (*OUTPUT_FILES, MANIFEST_FILE)
        ]
        (temp_dir / CHECKSUM_FILE).write_text("".join(checksum_lines), encoding="ascii")
        verify_bundle(temp_dir, enforce_directory_name=False)
        temp_dir.replace(destination)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return destination


def _parse_checksums(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line_number, line in enumerate(path.read_text(encoding="ascii").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._-]+)", line)
        if not match:
            raise EvidenceError(f"checksums line {line_number}: invalid format")
        digest, name = match.groups()
        if name in result:
            raise EvidenceError(f"checksums line {line_number}: duplicate file")
        result[name] = digest
    return result


def verify_bundle(
    bundle: Path, *, enforce_directory_name: bool = True
) -> dict[str, Any]:
    bundle = bundle.resolve()
    if not bundle.is_dir():
        raise EvidenceError(f"bundle directory does not exist: {bundle}")
    symlinks = [item.name for item in bundle.iterdir() if item.is_symlink()]
    if symlinks:
        raise EvidenceError(f"bundle symlinks are not allowed: {sorted(symlinks)}")
    actual_files = {item.name for item in bundle.iterdir() if item.is_file()}
    child_dirs = [item.name for item in bundle.iterdir() if item.is_dir()]
    if actual_files != ALLOWED_BUNDLE_FILES or child_dirs:
        raise EvidenceError(
            f"bundle contents invalid; files={sorted(actual_files)}, directories={sorted(child_dirs)}"
        )
    manifest = _read_json(bundle / MANIFEST_FILE)
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version", "run_id", "privacy", "provenance", "exporter", "files"
    }:
        raise EvidenceError("manifest fields do not match the evidence contract")
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise EvidenceError("unsupported evidence schema version")
    if enforce_directory_name and bundle.name != manifest["run_id"]:
        raise EvidenceError("manifest run_id does not match its directory")
    _validate_metadata(manifest["provenance"])
    privacy = manifest["privacy"]
    expected_privacy = {
        "classification": "public-sanitized",
        "contains_questions": False,
        "contains_database_rows": False,
        "contains_sql_text": False,
        "contains_provider_payloads": False,
        "contains_credentials": False,
        "contains_absolute_paths": False,
    }
    if privacy != expected_privacy:
        raise EvidenceError("manifest privacy declaration is invalid")
    _check_structure(manifest, context="manifest")
    _sanitise_config(_read_json(bundle / "config.json"), strip_credentials=False)
    _sanitise_scores(_read_json(bundle / "scores.json"), strip_sample_records=False)
    _read_diagnostics(bundle / "sample-diagnostics.jsonl")
    _safe_text((bundle / "report.md").read_text(encoding="utf-8"), context="report")

    checksums = _parse_checksums(bundle / CHECKSUM_FILE)
    expected_checksum_names = set((*OUTPUT_FILES, MANIFEST_FILE))
    if set(checksums) != expected_checksum_names:
        raise EvidenceError("checksums file list is invalid")
    for name, digest in checksums.items():
        if _sha256(bundle / name) != digest:
            raise EvidenceError(f"checksum mismatch: {name}")
    if manifest["files"] != {name: checksums[name] for name in OUTPUT_FILES}:
        raise EvidenceError("manifest file checksums are invalid")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="create a sanitized evidence bundle")
    export.add_argument("--run-id", required=True)
    export.add_argument("--metadata", required=True, type=Path)
    export.add_argument("--config", required=True, type=Path)
    export.add_argument("--scores", required=True, type=Path)
    export.add_argument("--report", required=True, type=Path)
    export.add_argument("--diagnostics", required=True, type=Path)
    export.add_argument(
        "--output-root", type=Path, default=Path("evidence/reported-results")
    )
    export.set_defaults(func=lambda args: print(export_bundle(args)))

    verify = subparsers.add_parser("verify", help="verify schema, privacy and checksums")
    verify.add_argument("bundle", type=Path)
    verify.set_defaults(func=lambda args: (verify_bundle(args.bundle), print(f"evidence OK: {args.bundle}")))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except EvidenceError as exc:
        raise SystemExit(f"evidence error: {exc}") from None


if __name__ == "__main__":
    main()
