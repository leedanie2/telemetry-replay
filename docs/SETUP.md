# Setup and verification

## Docker / Compose (recommended)

Use a running Docker engine with the Compose v2 plugin. From a fresh clone:

```sh
docker compose run --build --rm app demo
```

The application image runs Python 3.11 as a non-root user. PostgreSQL 18 runs on
an internal Compose network, without a published host database port. Demo data is
created in a temporary schema and rolled back. The PostgreSQL volume persists.

Run checks equivalent to CI:

```sh
docker compose build app
docker compose run --rm --no-deps --entrypoint python app -m unittest discover -s tests -v
docker compose up -d --wait --wait-timeout 90 postgres
docker compose run --rm -e TELEMETRY_INTEGRATION=1 --entrypoint python app -m unittest discover -s tests -v
```

The first test invocation runs 11 unit tests and skips 14 database tests. The
second runs all 25 tests. Integration tests create and remove random schemas;
connection failures fail the run. Tests never silently replace application tables.

For persistent data, use the README's init/register/replay/report commands.
`docker compose down` stops containers and preserves data. Only if deliberately
resetting this project's disposable database, use `docker compose down --volumes`.

## Native Python / PostgreSQL

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Python 3.9+ works with the pinned dependencies; the Docker path standardizes on
3.11. Set DATABASE_URL to a PostgreSQL connection string for your development
instance. Keep real credentials outside Git. The demo/tests require schema-creation
privileges; the app's deployment privilege model is outside this project’s scope.

On Apple Silicon macOS, the optional helper uses Homebrew PostgreSQL 18:

```sh
brew install postgresql@18
bash scripts/db.sh init
bash scripts/db.sh start
bash scripts/db.sh create
```

These initialize a new project-local cluster once, on a private Unix socket at
.local/socket. Set PG_BIN for a different installation. Later use start/stop/status;
never rerun init on an existing cluster. No login service is enabled.

```sh
make db-check
make demo
make test
make integration
```

For persistent native use, run `.venv/bin/python -m telemetry init-db` once and
register devices before replay. CLI help lists all commands. Use the virtual
environment interpreter, not an unrelated Conda/system Python without dependencies.

## Regenerate load measurements

Run from the repository root with Python dependencies and PostgreSQL available:

```sh
.venv/bin/python scripts/generate_load.py data/generated/load.csv --base-rows 100000
.venv/bin/python -m scripts.measure data/generated/load.csv \
  --output artifacts/benchmark.json --base-rows 100000 --repeats 3 \
  --hardware 'YOUR machine model, CPU, RAM, OS, connection type'
```

Choose new paths if files exist. The generator creates 100,000 distinct readings
plus 10,000 exact retries and 1,000 conflicting deliveries. The measurement script
creates a fresh isolated schema for each repetition, verifies actual stored/audit
counts and known report values, compares a bounded query with/without the event
index, and removes only its own schemas. It leaves application data untouched.
The fixed range-query fixture requires at least 30,000 base readings.

Raw output records the input hash, code revision, versions, timing scope, all
repetitions, and query plans. Generated data and ad hoc results are ignored by Git.
The generic `telemetry benchmark` command instead measures the current application
database; it does not create devices, reset state, or independently verify answers.

## Troubleshooting

- Missing psycopg: use `.venv/bin/python` and install requirements in that environment.
- Database unavailable: start the server; confirm DATABASE_URL or project socket.
- Existing tables on init: use replay/report, or choose a fresh database. No automatic reset.
- Unexpected empty report: confirm device IDs; omit --device to inspect all devices.
- Docker output permissions: mount a directory writable by container UID 10001
  for generated files. Source files in /app are intentionally read-only to that user.
