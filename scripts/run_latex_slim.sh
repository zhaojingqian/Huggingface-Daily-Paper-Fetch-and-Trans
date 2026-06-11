#!/usr/bin/env bash
# Start the optional slim LaTeX translation container.
#
# The production container is not touched. Use GPT_ACADEMIC_CONTAINER to point
# a single command at this canary container.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${GPT_ACADEMIC_SLIM_IMAGE:-paper-trans-latex-slim:latest}"
CONTAINER="${GPT_ACADEMIC_SLIM_CONTAINER:-gpt-academic-latex-slim}"
CONFIG="${GPT_ACADEMIC_CONFIG:-/root/workspace/gpt-academic/config_private.py}"
MEMORY="${GPT_ACADEMIC_SLIM_MEMORY:-1400m}"
MEMORY_SWAP="${GPT_ACADEMIC_SLIM_MEMORY_SWAP:-3000m}"

if [ ! -f "$CONFIG" ]; then
  echo "[slim-run] ERROR: config file not found: ${CONFIG}" >&2
  exit 1
fi

if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "[slim-run] container already running: ${CONTAINER}"
else
  if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    echo "[slim-run] starting existing container: ${CONTAINER}"
    docker start "$CONTAINER" >/dev/null
  else
    echo "[slim-run] creating container: ${CONTAINER}"
    docker run -d \
      --name "$CONTAINER" \
      --network=host \
      --restart unless-stopped \
      --memory="$MEMORY" \
      --memory-swap="$MEMORY_SWAP" \
      -v "${CONFIG}:/gpt/config_private.py:ro" \
      "$IMAGE" >/dev/null
  fi
fi

if [ "${GPT_ACADEMIC_SKIP_SETUP:-0}" = "1" ]; then
  echo "[slim-run] skipping setup_docker_env.sh (GPT_ACADEMIC_SKIP_SETUP=1)"
else
  echo "[slim-run] applying paper-trans LaTeX environment patches"
  GPT_ACADEMIC_CONTAINER="$CONTAINER" bash "${ROOT_DIR}/scripts/setup_docker_env.sh"
fi

echo "[slim-run] ready: ${CONTAINER}"
docker ps --filter "name=^/${CONTAINER}$" --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'
