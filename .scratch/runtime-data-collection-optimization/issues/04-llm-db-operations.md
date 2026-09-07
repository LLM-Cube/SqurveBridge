# LLM and database operations

Status: implemented

Synchronous and streaming model entry points record logical calls and observable attempts. Provider-reported usage retains call/attempt provenance. Database execution records monotonic duration, execution, fetch, row-count and payload-preservation completeness; full result payload retention is opt-in.

Acceptance: LLM usage linkage, non-overlapping usage aggregation and DB preservation tests pass. Provider-hidden retries remain explicitly opaque.
