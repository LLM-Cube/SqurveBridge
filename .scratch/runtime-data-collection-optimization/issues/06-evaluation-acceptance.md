# Evaluation dependency and acceptance integration

Status: implemented

Offline evaluation checks each metric's evidence dependencies independently and applies a versioned acceptance profile. Missing cost evidence does not invalidate a computable correctness metric, while missing required metrics rejects the run.

Acceptance: independent metric computability and strict acceptance tests pass.
