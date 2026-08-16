#!/usr/bin/env bash
# Canary-run the slim LaTeX container without changing production data/indexes.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/load_workspace_env.sh
source "${SCRIPT_DIR}/load_workspace_env.sh"
ROOT_DIR="$PAPER_TRANS_ROOT"
CONTAINER="${GPT_ACADEMIC_SLIM_CONTAINER:-gpt-academic-latex-slim}"
OUT_DIR="${GPT_ACADEMIC_SLIM_CANARY_OUT:-/tmp/paper-trans-latex-slim-canary}"
SOURCE_CACHE_DIR="${PAPER_TRANS_CANARY_SOURCE_CACHE:-${XDG_CACHE_HOME:-/root/.cache}/paper-trans/canary-sources}"
MODE="${GPT_ACADEMIC_SLIM_CANARY_MODE:-compile}"
PYTHON="$PAPER_TRANS_PYTHON"
IDS=("$@")

if [ "${#IDS[@]}" -eq 0 ]; then
  if [ "$MODE" = "full" ]; then
    IDS=(2606.08432)
  else
    IDS=(2606.09967 2606.10917 2606.09828 2606.02060)
  fi
fi

mkdir -p "$OUT_DIR" "$SOURCE_CACHE_DIR"

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
  source_tar="${SOURCE_CACHE_DIR}/${aid}.tar"
  if [ -s "$source_tar" ]; then
    docker exec -u root "$CONTAINER" mkdir -p \
      "/gpt/gpt_log/arxiv_cache/${aid}/e-print"
    docker cp "$source_tar" \
      "${CONTAINER}:/gpt/gpt_log/arxiv_cache/${aid}/e-print/${aid}.tar"
    docker exec -u root "$CONTAINER" chown -R gptuser:gptuser \
      "/gpt/gpt_log/arxiv_cache/${aid}"
    echo "[slim-canary] reused source cache: ${source_tar}"
  fi
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
    "$PYTHON" "${ROOT_DIR}/translate_full.py" "$aid" -o "$OUT_DIR" "${extra_args[@]}" --timeout 3600
  pdf="${OUT_DIR}/${aid}_zh.pdf"
  if [ ! -s "$pdf" ]; then
    echo "[slim-canary] ERROR: missing output PDF: ${pdf}" >&2
    exit 1
  fi
  ls -lh "$pdf"
  if [ ! -s "$source_tar" ]; then
    container_tar="/gpt/gpt_log/arxiv_cache/${aid}/e-print/${aid}.tar"
    if docker exec "$CONTAINER" test -s "$container_tar"; then
      docker cp "${CONTAINER}:${container_tar}" "${source_tar}.tmp"
      mv "${source_tar}.tmp" "$source_tar"
      echo "[slim-canary] cached source: ${source_tar}"
    fi
  fi
  docker exec "$CONTAINER" rm -rf \
    "/gpt/gpt_log/arxiv_cache/${aid}" \
    /gpt/gpt_log/default_user \
    /gpt/gpt_log/admin
done

echo "[slim-canary] all canary translations succeeded"
