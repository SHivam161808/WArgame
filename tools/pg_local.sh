#!/usr/bin/env bash
# Project-local PostgreSQL cluster (no sudo, no system config):  tools/pg_local.sh init|start|stop|status
# Data lives in data/pg, listens on 127.0.0.1:${PGPORT:-5433}. Database "wargame", role "wargame".
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PGBIN="${PGBIN:-$(ls -d /usr/lib/postgresql/*/bin 2>/dev/null | sort -V | tail -1)}"
PGDATA="$ROOT/data/pg"
PORT="${PGPORT:-5433}"
SOCK="$ROOT/data/pg-sock"
case "${1:-start}" in
  init)
    [ -f "$PGDATA/PG_VERSION" ] && { echo "cluster already initialised"; exit 0; }
    mkdir -p "$PGDATA" "$SOCK"
    "$PGBIN/initdb" -D "$PGDATA" -U wargame --auth=trust -E UTF8 >/dev/null
    echo "listen_addresses = '127.0.0.1'" >> "$PGDATA/postgresql.conf"
    echo "port = $PORT" >> "$PGDATA/postgresql.conf"
    echo "unix_socket_directories = '$SOCK'" >> "$PGDATA/postgresql.conf"
    "$PGBIN/pg_ctl" -D "$PGDATA" -l "$ROOT/data/pg.log" -w start >/dev/null
    "$PGBIN/createdb" -h 127.0.0.1 -p "$PORT" -U wargame wargame
    echo "PostgreSQL ready: postgres://wargame@127.0.0.1:$PORT/wargame" ;;
  start) "$PGBIN/pg_ctl" -D "$PGDATA" -l "$ROOT/data/pg.log" -w status >/dev/null 2>&1 || "$PGBIN/pg_ctl" -D "$PGDATA" -l "$ROOT/data/pg.log" -w start ;;
  stop) "$PGBIN/pg_ctl" -D "$PGDATA" -w stop ;;
  status) "$PGBIN/pg_ctl" -D "$PGDATA" status ;;
  *) echo "usage: $0 init|start|stop|status"; exit 1 ;;
esac
