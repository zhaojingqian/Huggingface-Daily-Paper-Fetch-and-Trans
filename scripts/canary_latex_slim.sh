#!/usr/bin/env bash
# Canary-run the slim LaTeX container without changing production data/indexes.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTAINER="${GPT_ACADEMIC_SLIM_CONTAINER:-gpt-academic-latex-slim}"
OUT_DIR="${GPT_ACADEMIC_SLIM_CANARY_OUT:-/tmp/paper-trans-latex-slim-canary}"
MODE="${GPT_ACADEMIC_SLIM_CANARY_MODE:-compile}"
IDS=("$@")

if [ "${#IDS[@]}" -eq 0 ]; then
  if [ "$MODE" = "full" ]; then
    IDS=(2606.08432)
  else
    IDS=(2606.09967 2606.10917 2606.09828 2606.02060)
  fi
fi

mkdir -p "$OUT_DIR"

if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  echo "[slim-canary] ERROR: container is not running: ${CONTAINER}" >&2
  echo "[slim-canary] Start it first: scripts/run_latex_slim.sh" >&2
  exit 1
fi

echo "[slim-canary] container: ${CONTAINER}"
echo "[slim-canary] output: ${OUT_DIR}"
echo "[slim-canary] mode: ${MODE}"
echo "[slim-canary] ids: ${IDS[*]}"

for aid in "${IDS[@]}"; do
  echo "[slim-canary] ==== ${aid} ===="
  extra_args=()
  if [ "$MODE" = "compile" ]; then
    extra_args+=(--keep-translation)
  elif [ "$MODE" = "full" ]; then
    extra_args+=(--no-cache)
  else
    echo "[slim-canary] ERROR: GPT_ACADEMIC_SLIM_CANARY_MODE must be compile or full" >&2
    exit 1
  fi

  GPT_ACADEMIC_CONTAINER="$CONTAINER" \
    python3 "${ROOT_DIR}/translate_full.py" "$aid" -o "$OUT_DIR" "${extra_args[@]}" --timeout 3600
  pdf="${OUT_DIR}/${aid}_zh.pdf"
  if [ ! -s "$pdf" ]; then
    echo "[slim-canary] ERROR: missing output PDF: ${pdf}" >&2
    exit 1
  fi
  ls -lh "$pdf"
  docker exec "$CONTAINER" rm -rf \
    "/gpt/gpt_log/arxiv_cache/${aid}" \
    /gpt/gpt_log/default_user \
    /gpt/gpt_log/admin
done

echo "[slim-canary] all canary translations succeeded"
