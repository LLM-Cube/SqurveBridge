# SqurveBridge

<div align="center">

<img src="assets/squrvebridge-icon.png" alt="SqurveBridge icon" width="140" />

**Turn released Text-to-SQL methods and databases into reproducible Squrve workflows**

Integrate · Reproduce · Diagnose · Improve

[![Project site](https://img.shields.io/badge/Project%20site-Vercel-000000.svg)](https://squrvebridge.vercel.app/)
[![Live Demo](https://img.shields.io/badge/Live%20Demo-Hugging%20Face-FFD21E.svg)](https://huggingface.co/spaces/zmmjjkk/SqurveBridge)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-2ea44f.svg)](LICENSE)
[![Upstream](https://img.shields.io/badge/Upstream-Squrve-6f42c1.svg)](https://github.com/LLM-Cube/Squrve)

[Reproduce in 30 Minutes](#reproduce-in-30-minutes) · [Architecture](#architecture) · [Features](#features) · [Demo](#demo) · [Docs](#documentation)

</div>

---

## Reproduce in 30 Minutes

SqurveBridge pins one environment and reproduces from source — no version
matrix: **Python 3.11** via `uv`, **Node.js 22.19+** (Demo only), and an API
key for the provider used by your chosen config.

```bash
# from your clone of this repository:
uv sync --python 3.11

uv run python tools/benchmarks.py download spider   # checksummed fetch from the HF dataset
uv run python tools/benchmarks.py install spider

cp .env.example .env   # add provider keys — never commit .env
uv run python reproduce/run.py spider c3sql
```

Scores and evidence land under `workspace/artifacts/`. Verify the toolchain
without any LLM calls:

```bash
uv run python tools/release_check.py --skip-history
```

Remote model calls may incur cost. Full walkthrough:
[Getting Started](docs/GETTING_STARTED.md) · Repository map:
[Project Structure](#project-structure).

## Architecture

SqurveBridge reconstructs released Text-to-SQL methods as inspectable **Squrve Actors**, normalizes benchmarks behind one contract, runs method–database pairs through reproducible configs, and persists sample- and stage-level evidence.

It is **not** another Text-to-SQL model — it is the bridge between released methods, new databases, and trustworthy evaluation. Built on upstream [Squrve](https://github.com/LLM-Cube/Squrve), with an embedded [Pi](https://github.com/earendil-works/pi) Agent as the Demo runtime kernel.

<div align="center">
<img src="assets/squrvebridge-framework.png" alt="SqurveBridge framework overview" width="95%" />
</div>

### Platform overview (ASCII)

```text
                         SqurveBridge Platform
  +====================================================================+
  |                                                                    |
  |  Inputs              Integration Harness                           |
  |  +-------------+     +------------------------------------------+  |
  |  | Existing    |     |  Method Adapter ----> Actor Pipeline     |  |
  |  | Methods     |---->|       |                     |            |  |
  |  +-------------+     |       v                     v            |  |
  |  | External    |     |  Benchmark Adapter -> Normalized Interface| |
  |  | Benchmarks  |---->|       |                     |            |  |
  |  +-------------+     |       +----------+----------+            |  |
  |  | Metrics     |     |                  v                       |  |
  |  +-------------+     |         Runnable Configuration           |  |
  |                      +-------------------+----------------------+  |
  |                                          |                         |
  |                                          v                         |
  |                               +---------------------+              |
  |                               |  Run & Generate SQL |              |
  |                               +----------+----------+              |
  |                                          |                         |
  |                                          v                         |
  |                      Unified Evaluation System                     |
  |                      +------------------------------------------+  |
  |                      |  L1 SQL Quality   |  L2 Runtime Cost     |  |
  |                      |  L3 Structure     |  L4 Errors           |  |
  |                      +-------------------+----------------------+  |
  |                                          |                         |
  |                                          v                         |
  |                      Recorded Evidence                             |
  |                      +------------------------------------------+  |
  |                      |  Scores  ·  Trace  ·  Report             |  |
  |                      +-------------------+----------------------+  |
  +====================================================================+
                                             |
                     optional                v
              +------------------------------------------------------+
              |     Metric-Guided Loop Engineering (Meta-Evo)        |
              |  Weakness Profile -> Scoped Candidate                |
              |       -> Smoke / Bounded Gate -> Full Confirmation   |
              |       -> Decision Record                             |
              |              |                                       |
              |              +-- accepted bounded update ------------>|
              |                  (feeds back into Runnable Config)   |
              +------------------------------------------------------+
```

**How to read the figure**

| Block | Role |
| --- | --- |
| **Inputs** | Community methods, external benchmarks, and metric definitions enter as candidates — not as opaque runtime dependencies. |
| **Integration Harness** | Method adapters rebuild released logic as native Actor pipelines; benchmark adapters expose a normalized data interface; both land in one **runnable configuration**. |
| **Run & Generate SQL** | The same Squrve Engine path used by CLI and Demo executes the configured task graph. |
| **Unified Evaluation** | Four evidence layers: SQL quality (L1), runtime cost (L2), structure (L3), error attribution (L4). |
| **Recorded Evidence** | Scores, workflow traces, and reports persist as inspectable bundles (publish only via `evidence/`). |
| **Meta-Evo (optional)** | Metric-guided loop: profile weaknesses → scoped edits → smoke gate → full confirmation → decision record; only accepted updates rewrite the runnable config. |

### End-to-end pipeline

```text
  candidate
      |
      v
  candidate-reader  --------->  manifest
      |
      v
  integration-pipeline  ----->  native Actors + benchmark registration
      |                         + reproduce/configs/<bench>/<method>.json
      v
  reproduce/run.py  --------->  isolated workspace/runs/<id>/
      |
      +-- Router  ->  DataLoader  ->  Engine (Task graph)
      |                                  |
      |                    +-------------+-------------+
      |                    v             v             v
      |               Reduce/Parse  Generate/Opt  Select/...
      |                    |             |             |
      |                    +-------------+-------------+
      |                                  |
      v                                  v
  stage metrics  ---------------->  scores.json + eval-store
      |
      +--(optional)-->  Meta-Evo  -->  bounded config/Actor update
```

### Runtime planes

Two planes share one set of project contracts; credentials and auth stay separate.

```text
  USER SURFACES
  +------------------+   +-------------------+   +------------------+
  | Reproduce CLI    |   | React Demo App    |   | Pi Agent chat    |
  | reproduce/run.py |   | demo-app/         |   | skills/ + Pi SDK |
  +--------+---------+   +---------+---------+   +--------+---------+
           |                       | REST/WS               |
           |                       v                       v
           |             +-------------------+   +------------------+
           |             | Flask API + jobs  |   | Pi bridge        |
           |             | demo/api_server   |   | demo/pi_*.py/mjs |
           |             +---------+---------+   +--------+---------+
           |                       |                      |
           v                       v                      v
  +--------------------------------------------------------------------+
  | PROJECT CONTRACTS                                                  |
  |  reproduce/configs/  config/  skills/  templates/  benchmarks/     |
  +--------------------------------+-----------------------------------+
                                   |
                                   v
  +--------------------------------------------------------------------+
  | SQURVE RUNTIME                                                     |
  |  Router -> DataLoader -> Engine -> Task graph -> Actor stages      |
  |  core/base.py  core/data_manage.py  core/engine.py  core/actor/    |
  +--------------------------------+-----------------------------------+
                                   |
                                   v
  +--------------------------------------------------------------------+
  | EVIDENCE                                                           |
  |  workspace/runs/  ->  metrics  ->  workspace/artifacts/            |
  |  reviewed publish only via evidence/reported-results/              |
  +--------------------------------------------------------------------+
```

| Module | Role |
| --- | --- |
| `Router` | Merge system defaults with a reproduce config |
| `DataLoader` | Normalize benchmark rows, schemas, DBs, and LLM adapters |
| `Engine` | Build and run checkpoint-aware Task graphs |
| `Actors` | Stage roles (reduce, parse, generate, optimize, …) |
| `Evaluation` | Stage/final metrics, diagnostics, four-layer scores |
| `Persistence` | Redacted configs, score bundles, eval-store |

The **reproduce config** is the main seam: CLI and Demo invoke the same runner with that contract, so browser runs and terminal runs do not diverge.

## Features

| Capability | What it provides |
| --- | --- |
| Native method integration | Released logic as Squrve Actors — not opaque repo wrappers |
| Benchmark adapters | Schema, questions, SQL, and splits behind one interface |
| Reproduce configs | Method + benchmark + sampling + evaluation in one file |
| Four-layer evidence | SQL quality, cost, structure, and error attribution |
| Pi Agent kernel | Embedded open-source Agent that loads Skills and drives Demo chat |
| Interactive workspace | Compose runs, inspect evidence, and chat with the agent |
| Optional Meta-Evo | Bounded improvement against recorded baselines |

## Demo

- **Live Demo** — [huggingface.co/spaces/zmmjjkk/SqurveBridge](https://huggingface.co/spaces/zmmjjkk/SqurveBridge)
- **Project site** — [squrvebridge.vercel.app](https://squrvebridge.vercel.app/)
- Hosted packaging notes: [deploy/huggingface/README.space.md](deploy/huggingface/README.space.md)
- Local Demo guide: [demo/README_EN.md](demo/README_EN.md)

**Local workspace:**

```bash
./demo/start.sh
# open http://127.0.0.1:5173
```

Local mode binds to `127.0.0.1` with full coding tools for Pi. The public Space is read-only for agent tools, blocks uploads and live evaluation writes, and keeps visitor credentials in session memory only — never in score bundles or runtime configs on disk.

## Usage

### Choose your agent runtime

Every SqurveBridge workflow is a Skill contract under `skills/`. The same
contracts run on three interchangeable agent runtimes — pick one:

| Runtime | Install | Invoke a Skill |
| --- | --- | --- |
| **Embedded Pi** (zero install) | `./demo/start.sh` | `/skill:candidate-reader <path>` in the Demo chat |
| **Claude Code** | `bash harness/install_squrve_harness.sh .` | `/candidate-reader <path>` |
| **Codex** | same command as Claude Code | `/candidate-reader <path>` |

**Embedded Pi** ships with the Demo backend (the pinned npm SDK
`@earendil-works/pi-coding-agent`) and loads `skills/` directly — no external
agent installation is required.

**Claude Code and Codex** share one installer. A single command creates flat
symlinks for both platforms at once, so `skills/` stays the single source of
truth:

```bash
bash harness/install_squrve_harness.sh .
# .claude/skills/<name> -> ../../skills/<name>   (Claude Code)
# .agents/skills/<name> -> ../../skills/<name>   (Codex)
```

The installer is idempotent: rerun it after adding or renaming a Skill, use
`--dry-run` to preview, and `--reconcile` to repair drifted links. Details:
[harness/README.md](harness/README.md).

### Run a method–benchmark pair

```bash
python reproduce/run.py <benchmark> <method>
# example:
python reproduce/run.py spider c3sql
```

Configs live under `reproduce/configs/<benchmark>/<method>.json`. The CLI and Demo job manager share the same runner.

### Integrate a candidate (method or database)

Use the Skill pipeline on any of the three runtimes above:

1. **candidate-reader** — inspect the candidate and produce a manifest  
2. **integration-pipeline** — native Actor / benchmark adapters + reproduce config  
3. **run** — debug, smoke, then full evaluation with evidence  

See [harness/README.md](harness/README.md) and `skills/*/SKILL.md`. Method work defaults to a feature branch or worktree; see [CONTRIBUTING.md](CONTRIBUTING.md).

### Inspect evidence

Published, checksummed examples: [evidence/](evidence/). Local runs write under `workspace/` (gitignored). Claims should cite verified score bundles, not ephemeral paths.

## Project Structure

```text
core/           Squrve runtime: Router, Engine, Tasks, Actors, LLM/DB adapters
reproduce/      Configs, CLI/batch runners, checkpoints
reproduce/eval/    Registry-driven evaluation: six metric layers, bundles, views
reproduce/evolve/  Meta-Evo engine: bounded MCTS search, fitness, journal
benchmarks/     Checksummed package manifest and installed benchmark layouts
demo/           Flask API, job manager, Pi bridge (pinned Pi SDK), deployment policy
demo-app/       React/Vite interactive workspace
skills/         Capability contracts (integration, run, Meta-Evo, …)
templates/      Schemas and skeletons for manifests, configs, reports
tools/          Deterministic validation, benchmarks, evidence, release gates
deploy/         Hugging Face Space packaging overlays
evidence/       Published, checksummed example score bundles
tests/          Python and Pi bridge regressions
workspace/      Runtime data only (gitignored except README)
```

Runtime layout under `workspace/` (override with `SQURVE_WORKSPACE_DIR`):

```text
workspace/
  sessions/    # Demo jobs, logs, Pi agentDir
  runs/        # reproduce intermediates + checkpoints
  artifacts/   # score bundles, eval-store.sqlite, evolve
  uploads/     # user databases and temp demo data
```

Nothing under `workspace/` is published. See [docs/REPRODUCIBILITY.md](docs/REPRODUCIBILITY.md).

## Configuration & Security

| Context | How credentials work |
| --- | --- |
| **Local** | Repo-root `.env` (gitignored). Prefer `${ENV:…}` refs in configs. |
| **Hosted Space** | No shared maintainer key. Session credentials stay in memory only. |
| **Artifacts** | API keys are redacted before any config is written to disk. |

Never commit `.env`, provider payloads, or plaintext keys. Security policy: [SECURITY.md](SECURITY.md). Scanners:

```bash
python tools/anonymity_scan.py
python tools/security_scan.py
```

## Development

```bash
# Python regressions
python -m unittest discover -s tests -p 'test_*.py' -v

# Frontend
npm ci --prefix demo-app
npm test --prefix demo-app
npm run build --prefix demo-app

# Full release gate (see CONTRIBUTING for --full)
uv run python tools/release_check.py --skip-history
```

Contribution rules (native Actors, branch isolation, PR gate): [CONTRIBUTING.md](CONTRIBUTING.md).  
Code of conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Documentation

| Doc | Topic |
| --- | --- |
| [Getting Started](docs/GETTING_STARTED.md) | Install, credentials, first run |
| [Reproducibility](docs/REPRODUCIBILITY.md) | Configs, checkpoints, evidence rules |
| [Benchmarks](docs/BENCHMARKS.md) | Packages, install, verification |
| [Demo guide](demo/README_EN.md) | Local interactive workspace |
| [Hosted Space](deploy/huggingface/README.space.md) | Public Live Demo packaging |
| [Harness](harness/README.md) | Skills, tools, templates, agent workflows |
| [Evidence](evidence/README.md) | Published score bundles |
| [Changelog](CHANGELOG.md) | Notable changes per release |
| [Security](SECURITY.md) · [Contributing](CONTRIBUTING.md) | Policy and contribution |

## License

MIT — see [LICENSE](LICENSE).

Upstream Squrve and integrated methods retain their own attribution; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The Pi Agent runtime is consumed as the pinned npm package `@earendil-works/pi-coding-agent` declared in `demo/package.json`.
