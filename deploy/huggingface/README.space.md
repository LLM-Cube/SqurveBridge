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

This Space runs the same Demo App shipped in the SqurveBridge repository: an interactive Text-to-SQL workspace with an embedded open-source Pi Agent backend, built on the upstream Squrve framework.

The hosted environment keeps live LLM-backed SQL generation, read-only execution, Pi chat, and project Skills. Pi is restricted to read-only tools in the public Space; full coding tools remain local-only.

The Space has no shared model credential. Use **Configure LLM** for the SQL
workflow and **Login to Pi** for Agent chat; the two credentials and provider
selections are independent. SQL credentials remain in server memory for the
browser session for at most 30 idle minutes. Pi credentials live only inside one
Pi child process and disappear when that chat session ends. A refresh or Space
restart may require re-entry. Never use production credentials in a public demo.

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
