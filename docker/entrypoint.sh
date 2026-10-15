#!/bin/sh
# Container start: wait for PostgreSQL, migrate, seed the admin user + catalogue, then serve HTTP + WebSockets.
set -e
cd /app

echo "[wargame] waiting for the database…"
python - <<'PY'
import os, sys, time
from urllib.parse import urlparse
import psycopg
u = urlparse(os.environ.get('DATABASE_URL', ''))
if not u.scheme.startswith('postgres'):
    sys.exit(0)
for i in range(60):
    try:
        psycopg.connect(host=u.hostname, port=u.port or 5432, user=u.username, password=u.password,
                        dbname=u.path.lstrip('/'), connect_timeout=3).close()
        sys.exit(0)
    except Exception as e:
        print(f'  database not ready yet ({type(e).__name__}), retry {i + 1}/60', flush=True)
        time.sleep(2)
sys.exit('[wargame] could not reach the database at ' + (u.hostname or '?'))
PY

# Cached elevation tiles shipped with the image → the persistent data volume (first start only)
if [ -d /app/data/tiles ] && [ "$DATA_DIR" != "/app/data" ]; then
    mkdir -p "$DATA_DIR/tiles"
    cp -rn /app/data/tiles/. "$DATA_DIR/tiles/" 2>/dev/null || true
fi

python manage.py migrate --noinput
python manage.py seed --demo

echo "[wargame] ready → open http://localhost:${HOST_PORT:-$PORT} (admin: ${ADMIN_USER:-admin})"
# Daphne runserver: HTTP + WebSockets on one port; --insecure serves /static/ with DEBUG=0
exec python manage.py runserver "0.0.0.0:${PORT}" --noreload --insecure
