#!/usr/bin/env bash
set -euo pipefail

ROLE="${SCREENWIRE_SERVICE_ROLE:-web}"
export PYTHONUNBUFFERED=1
export PYTHONDONTWRITEBYTECODE=1
export SCREENWIRE_PROJECTS_ROOT="${SCREENWIRE_PROJECTS_ROOT:-/data/projects}"
export SCREENWIRE_LOG_DIR="${SCREENWIRE_LOG_DIR:-/data/logs}"
mkdir -p "${SCREENWIRE_PROJECTS_ROOT}" "${SCREENWIRE_LOG_DIR}" /data/tmp
export TMPDIR="${TMPDIR:-/data/tmp}"
export TMP="${TMP:-/data/tmp}"
export TEMP="${TEMP:-/data/tmp}"

if [[ "${ROLE}" == "worker" ]]; then
  exec python3 workers/supabase_pipeline_worker.py
fi

export SW_PORT="${PORT:-${SW_PORT:-8000}}"
exec python3 server.py
