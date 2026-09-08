"""Declarative specifications for metrics, slices, and layers.

A dimension is data, not code: adding a metric means registering one
``MetricSpec``. Downstream views (evidence export, eval-store schema, report
rendering, Meta-Evo fitness) derive their field sets from the registry instead
of maintaining parallel allowlists.
"""

from __future__ import annotations

from dataclasses import dataclass, field


LAYERS = ("L1", "L2", "L3", "L4", "L5", "L6")

# How per-sample values fold into one aggregate cell.
AGGREGATIONS = (
    "mean",          # arithmetic mean of numeric values
    "rate",          # mean of 0/1 outcomes (Bernoulli)
    "sum",           # total over samples
    "percentile",    # percentile over numeric values, e.g. "percentile:95"
    "distribution",  # categorical value counts with percentages
    "derived",       # computed by a dedicated builder, not the engine
    "matrix",        # computed by the cross-run matrix view (L6)
)

INTERVALS = ("wilson", "bootstrap")

PUBLICATIONS = (
    "public",          # exported per sample and in aggregate
    "aggregate_only",  # exported in aggregate form only
    "private",         # never leaves the local bundle
)


@dataclass(frozen=True)
class MetricSpec:
    id: str
    layer: str
    source: str                    # dot-path into the per-sample record, or "derived:<key>"
    aggregation: str               # one of AGGREGATIONS, percentile as "percentile:<p>"
    unit: str = "ratio"            # ratio | tokens | seconds | count | label
    higher_is_better: bool = True
    interval: str | None = None    # one of INTERVALS or None
    publication: str = "public"
    sliceable: bool = True
    description: str = ""
    protocol_id: str = "legacy.project-metric"
    protocol_version: str = "1"
    candidate_scope: str = "legacy_implicit"
    availability: str = "computed_when_evidence_available"

    def __post_init__(self) -> None:
        if self.layer not in LAYERS:
            raise ValueError(f"{self.id}: invalid layer {self.layer!r}")
        base = self.aggregation.split(":", 1)[0]
        if base not in AGGREGATIONS:
            raise ValueError(f"{self.id}: invalid aggregation {self.aggregation!r}")
        if base == "percentile":
            percentile = self.aggregation.partition(":")[2]
            if not percentile or not percentile.replace(".", "", 1).isdigit():
                raise ValueError(f"{self.id}: percentile aggregation needs a number, got {self.aggregation!r}")
        if self.interval is not None and self.interval not in INTERVALS:
            raise ValueError(f"{self.id}: invalid interval {self.interval!r}")
        if self.publication not in PUBLICATIONS:
            raise ValueError(f"{self.id}: invalid publication {self.publication!r}")

    @property
    def engine_computed(self) -> bool:
        """Whether the generic aggregation engine can fold this metric."""
        return self.aggregation.split(":", 1)[0] in {"mean", "rate", "sum", "percentile", "distribution"}


@dataclass(frozen=True)
class SliceSpec:
    id: str
    field: str                     # per-sample field holding the slice label
    values: tuple[str, ...] = ()   # fixed label order; empty = discover from data
    min_samples: int = 1           # below this, a cell reports n only
    description: str = ""


@dataclass(frozen=True)
class LayerSpec:
    id: str
    title: str
    metrics: tuple[str, ...] = field(default_factory=tuple)
