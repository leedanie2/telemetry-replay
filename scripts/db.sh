#!/bin/bash
# Optional local PostgreSQL cluster management for development.
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
pg_bin="${PG_BIN:-/opt/homebrew/opt/postgresql@18/bin}"
local_dir="$repo_root/.local"
data_dir="$local_dir/postgres"
socket_dir="$local_dir/socket"
pg_port=55432

case "${1:-}" in
  init)
    mkdir -p "$socket_dir"
    chmod 700 "$local_dir" "$socket_dir"
    "$pg_bin/initdb" -D "$data_dir" -U telemetry --auth=trust --encoding=UTF8 --locale=C
    ;;
  start)
    # Socket-only access; the private directory restricts access to this OS user.
    "$pg_bin/pg_ctl" -D "$data_dir" -l "$local_dir/postgres.log" \
      -o "-h '' -k '$socket_dir' -p $pg_port" -w start
    ;;
  stop)
    "$pg_bin/pg_ctl" -D "$data_dir" -m fast -w stop
    ;;
  status)
    "$pg_bin/pg_ctl" -D "$data_dir" status
    ;;
  create)
    "$pg_bin/createdb" -h "$socket_dir" -p "$pg_port" -U telemetry telemetry
    ;;
  sql)
    shift
    "$pg_bin/psql" -X -v ON_ERROR_STOP=1 -h "$socket_dir" -p "$pg_port" \
      -U telemetry -d telemetry "$@"
    ;;
  *)
    echo 'Usage: bash scripts/db.sh {init|start|stop|status|create|sql [psql options]}' >&2
    exit 2
    ;;
esac
