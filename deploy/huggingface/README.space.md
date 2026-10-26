---
title: SqurveBridge Live Demo
emoji: 🌉
colorFrom: indigo
colorTo: blue
sdk: docker
app_port: 7860
pinned: true
license: mit
---

# SqurveBridge Live Demo

This Space runs the same Demo App shipped in the SqurveBridge repository: an interactive Text-to-SQL workspace built on the upstream Squrve framework.

The hosted environment keeps live LLM-backed SQL generation, read-only execution, and experimental evaluation runs. Design and run small Text-to-SQL experiments with up to 20 questions per cell using your own LLM credentials.

The Space has no shared model credential. Use **Configure LLM** to set your SQL provider; this same credential is used for both interactive queries and experiment runs. Session credentials remain in server memory for at most 30 idle minutes. A refresh or Space restart may require re-entry. Never use production credentials in a public demo.

Runtime data is written under `SQURVE_WORKSPACE_DIR` (default `/app/workspace`).
Without attached persistent storage, that directory is ephemeral and is cleared
when the Space rebuilds. To keep session artifacts across restarts, mount
storage and set `SQURVE_WORKSPACE_DIR=/data/workspace`.

## Deployment source

The Space is published automatically from the reviewed `main` branch of
`LLM-Cube/SqurveBridge`. GitHub Actions first runs the repository quality gates
and builds the explicit allowlisted Space context; only that generated context
is uploaded. Authentication uses a Hugging Face Trusted Publisher and a
short-lived, Space-scoped OIDC token, so no long-lived Hub token is stored in
GitHub.
