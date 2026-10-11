#!/usr/bin/env bash
# Start PostgreSQL (project-local cluster) and the Django/Daphne dev server on :8000.
set -euo pipefail
cd "$(dirname "$0")/.."
tools/pg_local.sh start >/dev/null
.venv/bin/python manage.py migrate --noinput >/dev/null
exec .venv/bin/python manage.py runserver 0.0.0.0:${PORT:-8000}
