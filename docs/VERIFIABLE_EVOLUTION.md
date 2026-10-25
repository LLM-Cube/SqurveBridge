# Verifiable evolution

Real worktree rollouts through the existing MCTS CLI use a versioned acceptance
policy and `VerifiedSimulator`. Synthetic scalar simulators remain useful for
algorithm tests and historical inspection; they are not verified measurements.
A historical summary without runtime evidence cannot enable strict promotion.

## Before execution

Use a local ignored directory under `workspace/` for the Evo artifacts. Initialize
the usual evolution journal, author executable actions and complete candidate
review. Each review ledger must have a clean approved round and an
`action_sha256` matching the complete serialized action (`action_hash` in
`reproduce.evolve.verified_search`). Scope C additionally requires the existing
human approval, recorded independently. A review is not evidence of improvement.

Supply `--repo-root`, `--smoke-command`, `--scores-path`, `--journal`,
`--evolve-dir`, `--stage` and `--policy-config` to the existing MCTS entrypoint.
The policy file includes an `acceptance` object and a `reviews` mapping from
action ID to local review ledger. The real CLI rejects absent acceptance policy.
Example acceptance contract (use the actual preselected sample IDs):

```json
{
  "version": "evo-acceptance-v1",
  "stage": "smoke",
  "required_metrics": ["ex"],
  "weights": {"ex": 1.0},
  "minimum_improvement": 0.0,
  "max_regression_rate": 0.0,
  "tolerance": 1e-9,
  "budget": {"rollouts": 2},
  "sample_selection": {"mode": "fixed_ids", "sample_ids": ["synthetic-0"]},
  "evidence_dependencies": {"ex": ["runtime"]},
  "collection": {
    "capture_sql": true,
    "capture_outputs": true,
    "evaluation_max_bytes": 67108864
  }
}
```

The CLI freezes its actual stage rollout budget. Policy files are immutable once
execution starts; changing the metric set, sample cohort, thresholds, weights or
base commit requires a fresh stage directory. The base ref is resolved to an
immutable commit before any worktree is created. Baseline and candidate must
share that commit/tree. Resumption creates fresh baseline versions and attempt
IDs, preserving earlier evidence. Executed attempts consume the stage budget;
review rejection spends no evaluation budget.

## What is retained

A real baseline and each candidate receive a distinct run ID and evidence root.
The reproduce runner passes that context to `Engine.execute`; batch runs use
separate run directories. Normal runs keep their prior behavior. The complete
executed configuration is redacted, hashed and bound into segment manifests;
it is not reconstructed from the smaller score-bundle config summary.

Each attempt keeps the reviewed action and ledger, actual cumulative Git diff,
base commit/tree, executed command and environment **key names**, config identity,
original score bundle, runtime segments and the separate offline evaluation
inputs. The transient worktree can then be removed. Retained paths inside the
attempt and all content hashes are sealed. Search selection events retain the
selected branch, eligible action pool, tree statistics, UCT parameters and the
deterministic selection rule; this does not prove an optimal search path.

SQL/output retention is explicitly opt-in. With either option off, necessary
context is unavailable and strict acceptance fails. SQLite snapshot copying is
bounded by `evaluation_max_bytes`; unsupported backends, missing candidates,
unknown conditions, incomplete collection or missing dependencies are gaps,
never inferred facts. All artifacts remain local; never publish raw databases,
SQL, questions, provider content, secrets or private paths.

Offline analysis is clearly separate from runtime: after the method finishes,
the evaluator uses its observed final candidate per sample and retained SQLite
snapshot to produce a typed v2 evaluation bundle. First-generation and oracle-over-pool metrics are not claimed by this final-output checkpoint. Ambiguous identical-SQL occurrences require an explicit selection identity or fail acceptance. The selected-final protocol,
model config, database contents, sample IDs and evaluator identity must match
between baseline and candidate. Every evaluated candidate must resolve to the
observed candidate occurrence and Actor sample span. Equal SQL from independent
generations retains distinct occurrence identities. Gold labels must also match.

## Acceptance and verification

Each active fitness term must be computable on the complete declared cohort.
Partial coverage does not become zero or a neutral bonus. Independent valid
inputs remain visible even when fitness is unavailable. The fitness is the
weighted sum of baseline-centered metric changes, bounded relative reductions
for cost/latency, minus the declared regression penalty. Thresholds, weights,
contributions, sample improvements and regressions are included in the result.

Positive cost weight requires usage evidence for every observed logical call;
positive latency weight requires complete Actor timing. Token observations keep
logical/attempt scope and source and are checked against runtime reports.
Missing or unsupported metrics (including unmeasured VES/R-VES) reject a profile
that requires them; weights are never silently redistributed. A negative measured
reward differs from the separate invalid-rollout scheduling control value.

Run `python tools/evolve_verify.py <attempt-directory>` locally. It checks hashes,
references, segment lifecycle, runtime/config/run/sample/candidate identities,
review and human gates, cohort compatibility, metric coverage, and the saved
decision. It recomputes the typed evaluation from retained execution results
with a saved-result executor, then recomputes fitness and acceptance. It makes
no model requests and executes no SQL. No valid evidence means nonzero exit.
Rejected, crashed and incomplete attempts remain inspectable with their failure
seal and reason; inspection alone never turns them into accepted evidence.

The actual search and local verifier use the same acceptance function. The
journal records the verification reference and scope; smoke evidence supports
smoke conclusions only. Pass separate stage-specific simulators into the existing
bounded funnel. Full rejection stops the funnel and cannot become a successful
full recommendation. Existing review and final human acceptance remain separate.
Explanations of a change remain hypotheses unless independently tested.
