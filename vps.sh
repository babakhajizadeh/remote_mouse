#!/usr/bin/env bash
# Manage the relay on Linux without root or a system service.
set -euo pipefail

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
state_dir="$project_dir/.run"
pid_file="$state_dir/relay.pid"
log_file="$state_dir/relay.log"
python_bin="${PYTHON:-python3}"

usage() {
    echo "Usage: bash vps.sh {start|stop|restart|status} [relay options]"
    echo "Example: bash vps.sh start --port 5000"
    echo "Relay options apply only to start/restart. Logs: $log_file"
}

action="${1:-help}"
if (( $# > 0 )); then shift; fi
case "$action" in
    help|-h|--help) usage; exit 0 ;;
    start|restart) ;;
    stop|status)
        if (( $# > 0 )); then usage >&2; exit 2; fi ;;
    *) usage >&2; exit 2 ;;
esac

if [[ "$(uname -s)" != Linux ]]; then
    echo "Run this script on the Linux VPS." >&2
    exit 1
fi
command -v flock >/dev/null || { echo "Missing flock (util-linux)." >&2; exit 1; }
umask 077
mkdir -p -- "$state_dir"
# Serialize start/stop commands. The relay must not inherit this lock.
exec 9>"$state_dir/relay.lock"
flock -x 9

# Check Linux's process start time as well as PID to avoid stopping a reused PID.
process_identity() {
    local stat_line
    local -a fields
    [[ "$1" =~ ^[1-9][0-9]*$ ]] || return 1
    [[ -r "/proc/$1/stat" ]] || return 1
    IFS= read -r stat_line < "/proc/$1/stat" || return 1
    read -r -a fields <<< "${stat_line##*) }"
    [[ "${fields[0]:-Z}" != Z && "${fields[0]:-X}" != X ]] || return 1
    printf '%s\n' "${fields[19]:-}"
}

running() {
    local actual_start
    pid=""
    saved_start=""
    [[ -f "$pid_file" ]] || return 1
    read -r pid saved_start < "$pid_file" || return 1
    [[ "$saved_start" =~ ^[0-9]+$ ]] || return 1
    actual_start="$(process_identity "$pid")" || return 1
    [[ "$actual_start" == "$saved_start" ]] && kill -0 "$pid" 2>/dev/null
}

start_relay() {
    if running; then
        echo "Relay already running (PID $pid). Use restart to change options."
        return 0
    fi
    command -v "$python_bin" >/dev/null || { echo "Python not found: $python_bin" >&2; return 1; }
    [[ -f "$project_dir/relay.py" && -f "$project_dir/protocol.py" ]] || {
        echo "Keep vps.sh, relay.py, and protocol.py in the same folder." >&2; return 1;
    }
    rm -f -- "$pid_file"
    touch -- "$log_file"
    local offset child child_start attempt
    offset=$(wc -c < "$log_file")
    nohup "$python_bin" -u "$project_dir/relay.py" "$@" >>"$log_file" 2>&1 < /dev/null 9>&- &
    child=$!
    if ! child_start="$(process_identity "$child")"; then
        echo "Relay failed to start. Check $log_file" >&2
        tail -n 10 -- "$log_file" >&2
        return 1
    fi
    printf '%s %s\n' "$child" "$child_start" > "$pid_file"
    for ((attempt=0; attempt<50; attempt++)); do
        if ! running; then
            rm -f -- "$pid_file"
            echo "Relay failed to start. Check $log_file" >&2
            tail -n 10 -- "$log_file" >&2
            return 1
        fi
        # Only inspect output from this launch, not an earlier successful run.
        if tail -c "+$((offset + 1))" -- "$log_file" | grep -F 'Relay listening on ' >/dev/null; then
            echo "Relay started (PID $pid). Log: $log_file"
            return 0
        fi
        sleep 0.1
    done
    echo "Relay process started (PID $pid), but readiness was not confirmed. Check $log_file" >&2
    return 1
}

stop_relay() {
    if ! running; then
        rm -f -- "$pid_file"
        echo "Relay is not running."
        return 0
    fi
    local stopped_pid="$pid" attempt
    kill -TERM "$pid" 2>/dev/null || true
    for ((attempt=0; attempt<50; attempt++)); do
        if ! running; then
            rm -f -- "$pid_file"
            echo "Relay stopped (PID $stopped_pid)."
            return 0
        fi
        sleep 0.1
    done
    echo "Relay did not stop within five seconds (PID $stopped_pid)." >&2
    return 1
}

case "$action" in
    start) start_relay "$@" ;;
    stop) stop_relay ;;
    restart) stop_relay; start_relay "$@" ;;
    status)
        if running; then
            echo "Relay is running (PID $pid). Log: $log_file"
        else
            echo "Relay is not running."
            exit 1
        fi ;;
esac
