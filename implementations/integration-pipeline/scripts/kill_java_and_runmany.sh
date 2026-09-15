#!/usr/bin/env bash
set -euo pipefail

dry_run=0
grace_seconds=3

usage() {
  cat <<'EOF'
Usage: ./kill_java_and_runmany.sh [--dry-run] [--grace-seconds N]

Kills:
  1) all processes with executable name "java"
  2) all processes whose command line contains "runmany" (case-insensitive)

Options:
  --dry-run          Only print matching processes, do not kill.
  --grace-seconds N  Seconds to wait after SIGTERM before SIGKILL (default: 3).
  -h, --help         Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run)
      dry_run=1
      shift
      ;;
    --grace-seconds)
      if [[ $# -lt 2 ]]; then
        echo "error: --grace-seconds requires a value" >&2
        exit 2
      fi
      grace_seconds="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if ! [[ "$grace_seconds" =~ ^[0-9]+$ ]]; then
  echo "error: --grace-seconds must be a non-negative integer" >&2
  exit 2
fi

declare -A pid_set=()

while IFS= read -r pid; do
  [[ -n "${pid}" ]] && pid_set["$pid"]=1
done < <(pgrep -x java || true)

while IFS= read -r pid; do
  [[ -n "${pid}" ]] && pid_set["$pid"]=1
done < <(pgrep -f -i runmany || true)

if [[ ${#pid_set[@]} -eq 0 ]]; then
  echo "No matching java/runmany processes found."
  exit 0
fi

pids=()
for pid in "${!pid_set[@]}"; do
  if [[ "$pid" != "$$" ]]; then
    pids+=("$pid")
  fi
done

if [[ ${#pids[@]} -eq 0 ]]; then
  echo "No matching java/runmany processes found (excluding current shell/script)."
  exit 0
fi

echo "Matching processes:"
for pid in "${pids[@]}"; do
  ps -p "$pid" -o pid=,comm= || true
done

if [[ "$dry_run" -eq 1 ]]; then
  echo "Dry run only. No processes were killed."
  exit 0
fi

echo "Sending SIGTERM to ${#pids[@]} process(es)..."
kill -TERM "${pids[@]}" 2>/dev/null || true

if [[ "$grace_seconds" -gt 0 ]]; then
  sleep "$grace_seconds"
fi

alive=()
for pid in "${pids[@]}"; do
  if kill -0 "$pid" 2>/dev/null; then
    alive+=("$pid")
  fi
done

if [[ ${#alive[@]} -eq 0 ]]; then
  echo "Done. All matching processes exited after SIGTERM."
  exit 0
fi

echo "Sending SIGKILL to ${#alive[@]} still-running process(es)..."
kill -KILL "${alive[@]}" 2>/dev/null || true
echo "Done."
