# Repository Guidelines

## Project Structure & Module Organization

`core/` contains Squrve runtime extensions and native Text-to-SQL Actors. Reproducible experiments live in `reproduce/`; benchmark contracts are in `benchmarks/`, and deterministic utilities in `tools/`. The Flask/Pi backend is in `demo/` (the Pi runtime is the pinned npm SDK `@earendil-works/pi-coding-agent` declared in `demo/package.json`), and the React/Vite interface in `demo-app/`. Deployment overlays live in `deploy/`, examples in `evidence/`, and Python regressions in `tests/`. Agent workflow contracts use `skills/`, `templates/`, and `harness/`. Local runtime data (Demo jobs, reproduce intermediates, uploads, score bundles) lives under `workspace/` and must not be committed except `workspace/README.md`.

This repository is the authoritative source for the real interactive demo
deployed as a configured Hugging Face Space. The separate
`squrvebridge-live-demo` repository is only the paper presentation
website on Vercel; never move this repository's runtime or Demo App into that
paper-site repository.

## Build, Test, and Development Commands

- `uv sync --python 3.11` installs the locked Python environment into `.venv/`.
- `uv run python -m unittest discover -s tests -p 'test_*.py' -v` runs all Python regressions.
- `uv run python tools/release_check.py --skip-history` validates anonymity, security, benchmark pointers, reproduce contracts, links, evidence, and tests.
- `npm ci --prefix demo-app` installs the locked frontend dependencies.
- `npm test --prefix demo-app` runs the Node test suite; `npm run build --prefix demo-app` creates the production bundle.
- `bash demo/build_embedded_pi.sh` installs the pinned embedded Pi SDK into `demo/node_modules`.
- `./demo/start.sh` launches the local API and UI; `./demo/stop.sh` stops both.

Python 3.11+ and Node.js 22.19+ are required for the embedded Pi backend.

## Coding Style & Naming Conventions

Use four-space Python indentation, type hints for public interfaces, `snake_case` functions/modules, and `PascalCase` classes. React components use `PascalCase`; JavaScript and TypeScript helpers use `camelCase`. Never patch the installed Pi SDK in `demo/node_modules`; adapt behavior inside `demo/pi_agent_bridge.mjs` or upgrade the pinned version instead. Match adjacent code and run `git diff --check`.

## Testing Guidelines

Python tests use `unittest` and follow `tests/test_<feature>.py`. Frontend tests use Node's built-in test runner with `*.test.js`. Add a regression before fixing behavior, then run the focused test and the release gate. Changes to evidence bundles must update their manifest hashes and pass `python tools/evidence.py verify <bundle>`.

## Commit & Pull Request Guidelines

Recent history uses concise Conventional Commit-style subjects: `feat:`, `fix:`, and `release:`. Keep one logical change per commit. Pull requests should explain motivation, affected workflows, verification commands, and compatibility risks; include screenshots for UI changes and link the relevant issue when available. Method Actor integrations require a feature branch or worktree; never modify `main` silently.

## Security & Publication Privacy

Never commit credentials, non-public personal information, personal contact
details, private URLs, absolute workstation paths, private manuscript details,
submission/venue/review information, internal roadmap material, benchmark
questions or SQL, provider payloads, or unpublished claims. User-approved
author names and affiliations are allowed.
Space secrets belong only in encrypted Hugging Face settings; never put them in
Git, example configs, browser storage, logs, screenshots, or fixtures. Runtime
configs and score-bundle `config.json` files must redact resolved API keys
before write. Use repository-relative links. Before publication, run
`python tools/anonymity_scan.py`, `python tools/security_scan.py`, and the
release gate. Also run `python tools/security_scan.py --history` and manually
inspect commit authors, deleted-but-reachable files, untracked files, benchmark
redistribution rights, raw question/SQL fixtures, sample identifiers, and
provider/model metadata. The configured GitHub and Hugging Face namespaces are
approved publication targets and are not themselves privacy findings.
SqurveBridge is the public project; Squrve is its upstream foundation.

## Agent skills

### Issue tracker

Issues and specs are tracked in GitHub Issues for `LLM-Cube/SqurveBridge`, using the `gh` CLI. See [docs/agents/issue-tracker.md](docs/agents/issue-tracker.md).

### Triage labels

Use `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, and `wontfix`. See [docs/agents/triage-labels.md](docs/agents/triage-labels.md).

### Domain docs

This is a single-context repository: read root domain vocabulary and relevant ADRs under `docs/adr/`. See [docs/agents/domain.md](docs/agents/domain.md).
