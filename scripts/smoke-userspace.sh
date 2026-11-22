#!/usr/bin/env bash
# Runs aosd CRUD only; does not enable kernel-control or run arbitrary agent commands.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"
WORK="$(mktemp -d)"
SERVICE_PID=''
cleanup() {
  if [ -n "$SERVICE_PID" ]; then
    kill "$SERVICE_PID" 2>/dev/null || true
    wait "$SERVICE_PID" 2>/dev/null || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT
cd "$ROOT"
export PYTHONPATH="$ROOT"
"$PYTHON" -m userspace.aosd.service --state-dir "$WORK/state" \
  --workspace-root "$WORK/workspaces" --socket "$WORK/aosd.sock" \
  --socket-group '' >"$WORK/service.log" 2>&1 &
SERVICE_PID=$!
for _ in $(seq 1 40); do
  if [ -S "$WORK/aosd.sock" ]; then break; fi
  if ! kill -0 "$SERVICE_PID" 2>/dev/null; then
    cat "$WORK/service.log" >&2
    exit 1
  fi
  sleep 0.1
done
[ -S "$WORK/aosd.sock" ] || { printf '%s\n' 'aosd socket was not created' >&2; exit 1; }
CLI=("$PYTHON" -m userspace.aosctl.cli --socket "$WORK/aosd.sock")
printf '%s\n' '=== status ==='
"${CLI[@]}" status
printf '%s\n' '=== create ==='
CREATE_JSON="$("${CLI[@]}" create --agent course-smoke-test --metadata '{"purpose":"crud"}')"
printf '%s\n' "$CREATE_JSON"
TASK_ID="$(printf '%s\n' "$CREATE_JSON" | "$PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["task"]["task_id"])')"
printf '%s\n' '=== get ==='
"${CLI[@]}" get "$TASK_ID"
printf '%s\n' '=== list ==='
"${CLI[@]}" list
printf '%s\n' '=== finish ==='
"${CLI[@]}" finish "$TASK_ID"
printf '%s\n' 'PASS: userspace aosd task CRUD only (no kernel ioctl)'
