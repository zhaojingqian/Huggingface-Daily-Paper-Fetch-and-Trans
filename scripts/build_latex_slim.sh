#!/usr/bin/env bash
# Build a smaller gpt-academic LaTeX translation image from the current /gpt code.
#
# This does not replace the production container. It only creates an image that
# can be tested with GPT_ACADEMIC_CONTAINER=gpt-academic-latex-slim.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_CONTAINER="${GPT_ACADEMIC_SOURCE_CONTAINER:-${GPT_ACADEMIC_CONTAINER:-gpt-academic-latex}}"
IMAGE="${GPT_ACADEMIC_SLIM_IMAGE:-paper-trans-latex-slim:latest}"
DOCKERFILE="${ROOT_DIR}/docker/latex-slim/Dockerfile"
CONTEXT="$(mktemp -d /tmp/paper-trans-latex-slim.XXXXXX)"

cleanup() {
  rm -rf "$CONTEXT"
}
trap cleanup EXIT

echo "[slim-build] source container: ${SOURCE_CONTAINER}"
echo "[slim-build] image: ${IMAGE}"
echo "[slim-build] context: ${CONTEXT}"

if ! docker ps --format '{{.Names}}' | grep -q "^${SOURCE_CONTAINER}$"; then
  echo "[slim-build] ERROR: source container is not running: ${SOURCE_CONTAINER}" >&2
  exit 1
fi

docker cp "${SOURCE_CONTAINER}:/gpt" "${CONTEXT}/gpt"

# Do not bake runtime cache or secrets into the image.
rm -rf \
  "${CONTEXT}/gpt/gpt_log" \
  "${CONTEXT}/gpt/.git" \
  "${CONTEXT}/gpt/config_private.py" \
  "${CONTEXT}/gpt/__pycache__"
find "${CONTEXT}/gpt" -type d -name '__pycache__' -prune -exec rm -rf {} +
find "${CONTEXT}/gpt" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete

docker build -f "$DOCKERFILE" -t "$IMAGE" "$CONTEXT"

echo "[slim-build] built image:"
docker images "$IMAGE"
