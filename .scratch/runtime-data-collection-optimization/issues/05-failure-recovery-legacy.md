# Failure, recovery and legacy evidence

Status: implemented

Collection is fail-open and bounded. Missing completion, sink failure, truncation, crash-compatible incomplete segments, resume linkage, immutable re-export and source-preserving legacy adaptation are represented without fabricated runtime facts.

Acceptance: sink failure, unobserved completion, truncation, resume, re-export and legacy-adaptation tests pass.
