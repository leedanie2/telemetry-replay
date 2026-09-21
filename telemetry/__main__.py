"""Command-line interface: python -m telemetry --help."""

import argparse
import csv
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import logging
import platform
from pathlib import Path
import sys
from time import perf_counter

import psycopg

from . import service
from .db import connect
from .io import parse_csv

LOG = logging.getLogger("telemetry")


def validate_counts(counts, input_rows):
    if not isinstance(counts, service.ReplayCounts):
        raise ValueError("ingest() must return ReplayCounts")
    values = asdict(counts)
    if any(type(value) is not int or value < 0 for value in values.values()):
        raise ValueError("Replay counts must be nonnegative integers")
    if sum(values.values()) != input_rows:
        raise ValueError("Replay counts do not account for every input row")
    return values


def parser():
    root = argparse.ArgumentParser(description="PostgreSQL telemetry replay and interval analysis")
    sub = root.add_subparsers(dest="command", required=True)
    sub.add_parser("check-db", help="Read-only database connectivity check")
    report_parser = sub.add_parser("report", help="Run SQL interval report")
    report_parser.add_argument("--device", default=None)
    sub.add_parser("init-db", help="Create schema once; never resets existing data")
    sub.add_parser("demo", help="Run an isolated correctness demo; leaves no data")
    register = sub.add_parser("register-device", help="Register device metadata")
    register.add_argument("device_id")
    register.add_argument("label")
    for name in ("inspect", "replay", "benchmark"):
        command = sub.add_parser(name)
        command.add_argument("csv", type=Path)
        if name == "benchmark":
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--state", choices=("empty", "populated"), required=True,
                                 help="Declared starting DB state; not auto-reset or verified")
            command.add_argument("--notes", required=True,
                                 help="Hardware model/RAM, setup, and transaction policy")
    return root


def run(args):
    readings = None
    if args.command in ("inspect", "replay", "benchmark"):
        started = perf_counter()
        payload = args.csv.read_bytes()
        readings = parse_csv(payload)
        parse_seconds = perf_counter() - started
        LOG.info("Parsed %d input records (arrival order preserved)", len(readings))
        if args.command == "inspect":
            return {"status": "parsed_only", "input_rows": len(readings),
                    "database_written": False}
    with connect() as connection:
        if args.command == "init-db":
            service.initialize(connection)
            return {"schema_created": True}
        if args.command == "register-device":
            service.register_device(connection, args.device_id, args.label)
            return {"device_registered": args.device_id}
        if args.command == "demo":
            from .demo import demo
            return demo(connection)
        if args.command == "check-db":
            database, user, version = connection.execute(
                "SELECT current_database(), current_user, version()"
            ).fetchone()
            return {"database": database, "user": user, "postgresql": version}
        if args.command == "report":
            return service.report(connection, device_id=args.device)
        if args.command == "benchmark":
            # Fail before modifying the DB if the destination already exists.
            if args.output.exists():
                raise ValueError("Benchmark output exists; choose a new filename")
        started = perf_counter()
        counts = validate_counts(service.ingest(connection, readings), len(readings))
        replay_seconds = perf_counter() - started
        result = {"status": "replayed", "input_rows": len(readings), **counts}
        if args.command == "replay":
            return result
        started = perf_counter()
        rows = service.report(connection)
        query_seconds = perf_counter() - started
        result.update({
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "input_sha256": hashlib.sha256(payload).hexdigest(),
            "declared_initial_state": args.state,
            "parse_seconds": parse_seconds,
            "replay_seconds": replay_seconds,
            "query_seconds": query_seconds,
            "report_rows": len(rows),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "postgresql": connection.execute("SELECT version()").fetchone()[0],
            "notes": args.notes,
            "timing_scope": "parse: CSV I/O+conversion; replay: transaction+count validation; "
                            "query: SQL+fetch; excludes connection, hashing, JSON output",
            "correctness_verified": False,
        })
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2)
            handle.write("\n")
        return result


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parser().parse_args(argv)
    try:
        result = run(args)
    except NotImplementedError as exc:
        LOG.error("%s", exc)
        return 3
    except psycopg.Error as exc:
        LOG.error("Database operation failed (%s, SQLSTATE %s)",
                  type(exc).__name__, exc.sqlstate or "unavailable")
        return 2
    except (ValueError, OSError, csv.Error) as exc:
        LOG.error("%s", exc)
        return 2
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
