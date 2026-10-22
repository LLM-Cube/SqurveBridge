#!/usr/bin/env bash
# Start SqurveBridge interactive demo (API + React workspace).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKSPACE_DIR="${SQURVE_WORKSPACE_DIR:-${ROOT}/workspace}"
RUNTIME_DIR="${WORKSPACE_DIR}/sessions/runtime"
API_PORT="${SQURVE_DEMO_API_PORT:-7861}"
WEB_PORT="${SQURVE_DEMO_WEB_PORT:-5173}"
API_HOST="${SQURVE_DEMO_API_HOST:-127.0.0.1}"
WEB_HOST="${SQURVE_DEMO_WEB_HOST:-127.0.0.1}"

mkdir -p "${RUNTIME_DIR}"

log() { printf '[squrve-demo] %s\n' "$*"; }
die() { printf '[squrve-demo] ERROR: %s\n' "$*" >&2; exit 1; }

cleanup_on_fail() {
  local code=$?
  if [[ "${code}" -ne 0 ]]; then
    log "Startup failed; cleaning up…"
    "${ROOT}/demo/stop.sh" >/dev/null 2>&1 || true
  fi
}
trap cleanup_on_fail EXIT

port_in_use() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"${port}" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

pid_alive() {
  local pid="$1"
  [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null
}

resolve_python() {
  if [[ -x "${ROOT}/.venv/bin/python" ]]; then
    echo "${ROOT}/.venv/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    die "Python not found. Create .venv at repo root first."
  fi
}

wait_http() {
  local url="$1"
  local label="$2"
  local tries="${3:-40}"
  local i
  for ((i = 1; i <= tries; i++)); do
    if curl -fsS --max-time 1 "${url}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  die "${label} did not become ready: ${url}"
}

# Launch outside the parent shell process group so IDE/tool cleanup
# cannot SIGTERM the demo when the starter command exits.
# Usage: daemonize <pid_file> <log_file> <cwd> <command...>
daemonize() {
  local pid_file="$1"
  local log_file="$2"
  local workdir="$3"
  shift 3
  "${PYTHON}" - "${pid_file}" "${log_file}" "${workdir}" "$@" <<'PY'
import subprocess
import sys
from pathlib import Path

pid_file = Path(sys.argv[1])
log_file = Path(sys.argv[2])
workdir = sys.argv[3]
command = sys.argv[4:]
log_file.parent.mkdir(parents=True, exist_ok=True)
with log_file.open("ab", buffering=0) as log:
    proc = subprocess.Popen(
        command,
        cwd=workdir,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
pid_file.write_text(f"{proc.pid}\n", encoding="utf-8")
print(proc.pid)
PY
}

cd "${ROOT}"

if [[ -f "${RUNTIME_DIR}/api.pid" ]] || [[ -f "${RUNTIME_DIR}/web.pid" ]] || [[ -f "${RUNTIME_DIR}/demo.env" ]]; then
  api_pid="$(cat "${RUNTIME_DIR}/api.pid" 2>/dev/null || true)"
  web_pid="$(cat "${RUNTIME_DIR}/web.pid" 2>/dev/null || true)"
  if { pid_alive "${api_pid}" || port_in_use "${API_PORT}"; } && { pid_alive "${web_pid}" || port_in_use "${WEB_PORT}"; }; then
    trap - EXIT
    # Report the ports the live instance actually bound, not the defaults.
    if [[ -f "${RUNTIME_DIR}/demo.env" ]]; then
      # shellcheck disable=SC1091
      source "${RUNTIME_DIR}/demo.env"
    fi
    log "Demo already running."
    log "  Workspace: http://${WEB_HOST}:${WEB_PORT}"
    log "  API:       http://${API_HOST}:${API_PORT}"
    log "Stop with:   demo/stop.sh"
    exit 0
  fi
  # Stale pid files / half-dead previous attempt — reclaim ports and restart.
  log "Cleaning stale demo runtime before restart…"
  "${ROOT}/demo/stop.sh" >/dev/null 2>&1 || true
fi

if port_in_use "${API_PORT}"; then
  trap - EXIT
  die "API port ${API_PORT} is already in use."
fi
if port_in_use "${WEB_PORT}"; then
  trap - EXIT
  die "Web port ${WEB_PORT} is already in use."
fi

PYTHON="$(resolve_python)"
command -v npm >/dev/null 2>&1 || die "npm not found. Install Node.js first."
command -v curl >/dev/null 2>&1 || die "curl not found."
"${PYTHON}" "${ROOT}/demo/runtime_check.py" || die \
  "Python runtime check failed. Install: uv sync --python ${PYTHON}"

node_major="$(node -p 'process.versions.node.split(".")[0]')"
[[ "${node_major}" -ge 22 ]] || die "Embedded Pi requires Node.js 22.19 or newer."

if [[ ! -d "${ROOT}/demo/node_modules/@earendil-works/pi-coding-agent/dist" ]]; then
  log "Installing embedded Pi SDK…"
  bash "${ROOT}/demo/build_embedded_pi.sh"
fi

if [[ ! -d "${ROOT}/demo-app/node_modules" ]]; then
  log "Installing frontend dependencies (npm ci)…"
  (cd "${ROOT}/demo-app" && npm ci)
fi

log "Starting API on http://${API_HOST}:${API_PORT}"
: >"${RUNTIME_DIR}/api.log"
api_pid="$(daemonize "${RUNTIME_DIR}/api.pid" "${RUNTIME_DIR}/api.log" "${ROOT}" \
  "${PYTHON}" "${ROOT}/demo/api_server.py" --host "${API_HOST}" --port "${API_PORT}")"

log "Starting frontend on http://${WEB_HOST}:${WEB_PORT}"
: >"${RUNTIME_DIR}/web.log"
# Prefer launching Vite directly so the recorded PID is the listener, not npm.
if [[ -x "${ROOT}/demo-app/node_modules/.bin/vite" ]]; then
  web_pid="$(daemonize "${RUNTIME_DIR}/web.pid" "${RUNTIME_DIR}/web.log" "${ROOT}/demo-app" \
    "${ROOT}/demo-app/node_modules/.bin/vite" --host "${WEB_HOST}" --port "${WEB_PORT}")"
else
  web_pid="$(daemonize "${RUNTIME_DIR}/web.pid" "${RUNTIME_DIR}/web.log" "${ROOT}/demo-app" \
    npm run dev -- --host "${WEB_HOST}" --port "${WEB_PORT}")"
fi

wait_http "http://${API_HOST}:${API_PORT}/api/health" "API"
wait_http "http://${WEB_HOST}:${WEB_PORT}/" "Frontend" 60

cat >"${RUNTIME_DIR}/demo.env" <<EOF
API_HOST=${API_HOST}
API_PORT=${API_PORT}
WEB_HOST=${WEB_HOST}
WEB_PORT=${WEB_PORT}
API_PID=${api_pid}
WEB_PID=${web_pid}
STARTED_AT=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
EOF

trap - EXIT
log "SqurveBridge demo is up."
log "  Workspace: http://${WEB_HOST}:${WEB_PORT}"
log "  API:       http://${API_HOST}:${API_PORT}"
case "$(printf '%s' "${SQURVE_DEMO_AGENT:-on}" | tr '[:upper:]' '[:lower:]')" in
  off|0|false|no) log "  Agent:     off (SQURVE_DEMO_AGENT)" ;;
  *) log "  Agent:     embedded Pi (${PI_AGENT_PROVIDER:-${SQURVE_LLM_PROVIDER:-auto}}/${PI_AGENT_MODEL:-${SQURVE_LLM_MODEL:-auto}})" ;;
esac
log "  Logs:      ${RUNTIME_DIR}/api.log , ${RUNTIME_DIR}/web.log"
log "Stop with:   demo/stop.sh"
