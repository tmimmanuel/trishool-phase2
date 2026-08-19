#!/usr/bin/env bash
# Forward Docker bridge IPs :8000 → 127.0.0.1:8000 when Halo is loopback-only.
# Lets OpenClaw (extra_hosts host.docker.internal:host-gateway) classify locally
# without binding Halo to 0.0.0.0 (this VPS has UFW inactive).
set -euo pipefail

PORT="${HALO_GUARD_PORT:-8000}"
TARGET="${HALO_GUARD_PROXY_TARGET:-127.0.0.1}"
BRIDGES="${HALO_GUARD_DOCKER_BRIDGES:-172.17.0.1 172.18.0.1}"

if ! command -v socat >/dev/null 2>&1; then
  echo "halo-guard-docker-proxy: socat is required" >&2
  exit 1
fi

pids=()
cleanup() {
  for p in "${pids[@]+"${pids[@]}"}"; do
    kill "$p" 2>/dev/null || true
  done
}
trap cleanup EXIT INT TERM

for ip in $BRIDGES; do
  echo "halo-guard-docker-proxy: ${ip}:${PORT} -> ${TARGET}:${PORT}"
  socat TCP-LISTEN:"${PORT}",bind="${ip}",fork,reuseaddr TCP:"${TARGET}:${PORT}" &
  pids+=("$!")
done

wait
