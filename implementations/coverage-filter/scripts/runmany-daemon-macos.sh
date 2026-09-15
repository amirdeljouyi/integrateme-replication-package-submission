#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
STATE_DIR="${RUNMANY_DAEMON_DIR:-$ROOT_DIR/.runmany-daemon}"
PID_FILE="$STATE_DIR/runmany.pid"
LOG_FILE="$STATE_DIR/runmany.log"
CMD_FILE="$STATE_DIR/runmany.cmd"

usage() {
  cat <<'EOF'
Usage:
  runmany-daemon-macos.sh start --cp "<classpath>" [--timeout-ms <ms>] <selector1> [selector2 ...]
  runmany-daemon-macos.sh stop
  runmany-daemon-macos.sh status
  runmany-daemon-macos.sh logs [--follow]

Notes:
  - This is a macOS-friendly detached runner using nohup/background mode.
  - State is written under .runmany-daemon/ by default.
  - Override state directory with RUNMANY_DAEMON_DIR.
EOF
}

ensure_state_dir() {
  mkdir -p "$STATE_DIR"
}

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -z "$pid" ]]; then
    return 1
  fi
  kill -0 "$pid" >/dev/null 2>&1
}

start_daemon() {
  if is_running; then
    echo "RunMany daemon already running (pid=$(cat "$PID_FILE"))."
    exit 0
  fi

  local classpath=""
  local timeout_ms=""
  local selectors=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --cp)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --cp"
          usage
          exit 1
        fi
        classpath="$2"
        shift 2
        ;;
      --timeout-ms)
        if [[ $# -lt 2 ]]; then
          echo "Missing value for --timeout-ms"
          usage
          exit 1
        fi
        timeout_ms="$2"
        shift 2
        ;;
      -*)
        echo "Unknown option: $1"
        usage
        exit 1
        ;;
      *)
        selectors+=("$1")
        shift
        ;;
    esac
  done

  if [[ -z "$classpath" ]]; then
    echo "Missing --cp \"<classpath>\""
    usage
    exit 1
  fi
  if [[ ${#selectors[@]} -eq 0 ]]; then
    echo "At least one selector is required."
    usage
    exit 1
  fi

  ensure_state_dir

  local -a cmd
  cmd=(java -cp "$classpath" app.RunMany)
  if [[ -n "$timeout_ms" ]]; then
    cmd+=(--timeout-ms "$timeout_ms")
  fi
  cmd+=("${selectors[@]}")

  {
    printf "Started at: %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
    printf "Command: "
    printf "%q " "${cmd[@]}"
    printf "\n\n"
  } >>"$LOG_FILE"

  nohup "${cmd[@]}" >>"$LOG_FILE" 2>&1 &
  local pid="$!"
  echo "$pid" >"$PID_FILE"
  printf "%q " "${cmd[@]}" >"$CMD_FILE"

  # Give it a short moment to fail fast on obvious startup issues.
  sleep 1
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    echo "RunMany daemon failed to start. Check logs:"
    echo "  $LOG_FILE"
    rm -f "$PID_FILE"
    exit 1
  fi

  echo "RunMany daemon started (pid=$pid)."
  echo "Logs: $LOG_FILE"
}

stop_daemon() {
  if ! is_running; then
    echo "RunMany daemon is not running."
    rm -f "$PID_FILE"
    exit 0
  fi

  local pid
  pid="$(cat "$PID_FILE")"
  kill "$pid" >/dev/null 2>&1 || true

  for _ in {1..20}; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      rm -f "$PID_FILE"
      echo "RunMany daemon stopped."
      return
    fi
    sleep 0.25
  done

  echo "RunMany daemon did not stop gracefully; sending SIGKILL."
  kill -9 "$pid" >/dev/null 2>&1 || true
  rm -f "$PID_FILE"
}

show_status() {
  if is_running; then
    echo "RunMany daemon is running (pid=$(cat "$PID_FILE"))."
    if [[ -f "$CMD_FILE" ]]; then
      echo "Command: $(cat "$CMD_FILE")"
    fi
    echo "Logs: $LOG_FILE"
  else
    echo "RunMany daemon is not running."
    if [[ -f "$CMD_FILE" ]]; then
      echo "Last command: $(cat "$CMD_FILE")"
    fi
    echo "Logs: $LOG_FILE"
  fi
}

show_logs() {
  local follow="${1:-}"
  ensure_state_dir
  touch "$LOG_FILE"

  if [[ "$follow" == "--follow" ]]; then
    tail -f "$LOG_FILE"
  else
    tail -n 200 "$LOG_FILE"
  fi
}

main() {
  if [[ $# -lt 1 ]]; then
    usage
    exit 1
  fi

  local action="$1"
  shift

  case "$action" in
    start) start_daemon "$@" ;;
    stop) stop_daemon ;;
    status) show_status ;;
    logs) show_logs "$@" ;;
    *)
      echo "Unknown action: $action"
      usage
      exit 1
      ;;
  esac
}

main "$@"
