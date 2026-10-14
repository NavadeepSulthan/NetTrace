#!/usr/bin/env bash
# Best-effort Linux triage. Inputs are data, never evaluated as shell code.
set -uo pipefail

HOST="${1:-127.0.0.1}"
REASON="${2:-Health check failed}"
OUT_DIR="${3:-./incidents}"
TIMESTAMP="$(date -u +"%Y%m%dT%H%M%SZ")"
SAFE_HOST="$(printf '%s' "$HOST" | tr -cs '[:alnum:].:_-' '_')"
mkdir -p -- "$OUT_DIR" || exit 2
REPORT_FILE="${OUT_DIR%/}/triage_${TIMESTAMP}_${SAFE_HOST}_$$.log"

section() { printf '\n\n[%s]\n' "$1" >> "$REPORT_FILE"; }
run_capture() {
  local label="$1"; shift
  section "$label"
  if command -v -- "$1" >/dev/null 2>&1; then
    # Diagnostics are individually bounded where `timeout` is available.
    if command -v timeout >/dev/null 2>&1; then
      timeout 8s "$@" >> "$REPORT_FILE" 2>&1 || printf 'Command failed or timed out: %s\n' "$1" >> "$REPORT_FILE"
    else
      "$@" >> "$REPORT_FILE" 2>&1 || printf 'Command failed: %s\n' "$1" >> "$REPORT_FILE"
    fi
  else
    printf 'Command not installed: %s\n' "$1" >> "$REPORT_FILE"
  fi
}

{
  printf '==========================================================\n'
  printf 'NETTRACE INCIDENT REPORT\n'
  printf 'Target  : %s\n' "$HOST"
  printf 'Trigger : %s\n' "$REASON"
  printf 'Time    : %s\n' "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  printf 'Host    : %s\n' "$(hostname 2>/dev/null || printf unknown)"
  printf '==========================================================\n'
} > "$REPORT_FILE"

run_capture 'SYSTEM LOAD' uptime
run_capture 'MEMORY (MiB)' free -m
run_capture 'NETWORK SOCKET SUMMARY' ss -s
if ! command -v ss >/dev/null 2>&1; then
  run_capture 'NETWORK SOCKET SUMMARY (fallback)' netstat -an
fi
section 'RESOLVER CONFIGURATION'
if [[ -r /etc/resolv.conf ]]; then
  grep -E '^[[:space:]]*nameserver' /etc/resolv.conf >> "$REPORT_FILE" || true
else
  printf '/etc/resolv.conf unavailable\n' >> "$REPORT_FILE"
fi
run_capture 'ICMP PING (5 packets)' ping -c 5 -W 1 -- "$HOST"
if command -v traceroute >/dev/null 2>&1; then
  run_capture 'ROUTE TRACE' traceroute -n -m 8 -q 1 -w 1 "$HOST"
elif command -v tracepath >/dev/null 2>&1; then
  run_capture 'ROUTE TRACE' tracepath -m 8 "$HOST"
else
  section 'ROUTE TRACE'
  printf 'Neither traceroute nor tracepath is installed\n' >> "$REPORT_FILE"
fi

printf 'Report generated at: %s\n' "$REPORT_FILE"
