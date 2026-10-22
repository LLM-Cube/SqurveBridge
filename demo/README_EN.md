# SqurveBridge Interactive Demo

The local workspace pairs the React application in `demo-app/` with the local
Flask API in `demo/api_server.py`.

## Start

From the repository root:

```bash
./demo/start.sh
```

Open `http://127.0.0.1:5173`. Stop the services with `./demo/stop.sh`.

## Views

- **SQL Studio** configures and runs a method-benchmark workflow.
- **Experiment Board** compares SQL quality, runtime cost, structure, and error
  evidence under a shared protocol.
- **Archive** opens persisted score bundles, traces, and reports.

The API binds to `127.0.0.1` by default. This is a local research interface; do not
expose it directly to an untrusted network.

## Showcase without the Agent

```bash
SQURVE_DEMO_AGENT=off ./demo/start.sh
```

The server then refuses every `/api/agent*` route and the page drops the Agent
pane. Integrated methods and benchmarks, side-by-side comparison and Actor swaps
still work: in Compose, swap a stage to another Actor of the same type to make a
**variant** that runs next to its method on the same samples. A variant changes
only that run's config; `reproduce/configs/` is never modified.

## Credentials

Copy `.env.example` to `.env` at the repository root and set the provider key used
by the selected configuration. Credentials are never displayed by the UI and must
not be committed.

## Manual Development Start

```bash
# Terminal A, from the repository root
.venv/bin/python demo/api_server.py --host 127.0.0.1 --port 7861

# Terminal B
cd demo-app
npm ci
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:7861`.
