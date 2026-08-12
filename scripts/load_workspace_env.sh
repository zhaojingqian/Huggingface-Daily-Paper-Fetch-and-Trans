#!/usr/bin/env bash
# Common host-runtime contract for directly invoked PaperHub shell scripts.
# Production entrypoints should normally use: workspace-ctl paper <command>.

WORKSPACE_ROOT="${WORKSPACE_ROOT:-/root/workspace}"

paperhub_load_env_file() {
  local env_file="$1"
  if [[ -f "$env_file" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$env_file"
    set +a
  fi
}

paperhub_load_env_file "$WORKSPACE_ROOT/.env"
paperhub_load_env_file "${PAPER_TRANS_ENV_FILE:-$WORKSPACE_ROOT/.env.d/paper.env}"

PAPER_TRANS_ROOT="${PAPER_TRANS_ROOT:-$WORKSPACE_ROOT/apps/paper-trans}"
PAPER_TRANS_PYTHON="${PAPER_TRANS_PYTHON:-${SERVER_PYTHON:-}}"

if [[ -z "$PAPER_TRANS_PYTHON" || ! -x "$PAPER_TRANS_PYTHON" ]]; then
  echo "PaperHub host Python is not configured or executable" >&2
  return 1 2>/dev/null || exit 1
fi

export WORKSPACE_ROOT PAPER_TRANS_ROOT PAPER_TRANS_PYTHON
