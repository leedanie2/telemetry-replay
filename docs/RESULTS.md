# Verification results

Measured September 21, 2026. All inputs are synthetic.

## Correctness and demo

25 automated tests pass against native PostgreSQL: 11 unit/CLI tests and 14 database
integration tests. Coverage includes eight hand-calculated fixtures, rollback of
both readings and audit entries, concurrent writers, device filtering, exact
counter arithmetic, retries after reconnection, and non-destructive initialization.

The [captured demo](evidence/demo.json) verifies six unique readings, an exact retry,
a conflicting payload, repeat replay, and unchanged persistent data. Its valid
interval subtotal is 51 J; energy spanning the reboot is unknown.

The [public CI workflow](https://github.com/leedanie2/telemetry-replay/actions/workflows/ci.yml)
builds the Docker image and runs unit tests, PostgreSQL integration tests, the demo,
and persistent CLI checks including device selection and an orderly database restart.
Docker CI runs Python 3.11; the native measurements below use Python 3.9.6.

## Replay load study

[Raw evidence and query plans](evidence/benchmark.json).
Measured source: [`ea41e30`](https://github.com/leedanie2/telemetry-replay/tree/ea41e30aa39e7d3c7746d9fefe94039576a88f0e),
with a clean working tree. Later documentation commits add this evidence; application
and measurement code are unchanged.

Environment: Apple M3, 16 GiB RAM, macOS 26.6.2, native PostgreSQL 18.6 over a local
Unix socket. Three repetitions, each with a fresh isolated schema. The running
server and OS caches were reused; these are not cold-cache measurements.

| Outcome per repetition | First replay | Repeated replay |
|---|---:|---:|
| Input deliveries | 111,000 | 111,000 |
| New measurements | 100,000 | 0 |
| Exact duplicates | 10,000 | 110,000 |
| Conflicting deliveries | 1,000 | 1,000 |
| Stored measurement rows afterward | 100,000 | 100,000 |
| Distinct audited conflicting payloads afterward | 1,000 | 1,000 |

The script independently checks final row counts, audit counts, and the generated
constant-power intervals: 12 J per 5 seconds, or 2.4 W. Boot-boundary energy remains
NULL. Report output contains 100,000 rows. These are observed checks, not just
counters returned by ingestion.

| Repetition | First replay (s) | Repeated replay (s) | Report + fetch (s) |
|---|---:|---:|---:|
| 1 | 5.296 | 10.940 | 0.480 |
| 2 | 5.049 | 10.762 | 0.454 |
| 3 | 5.169 | 11.154 | 0.469 |
| **Median** | **5.169** | **10.940** | **0.469** |

Replay timing includes commit. Report timing includes SQL, fetching all rows, and
Python conversion. Parsing (once: 0.316 s), connection setup,
schema setup, JSON serialization, and output are excluded. Repeated replay is
slower because existing keys require an attempted INSERT and a payload SELECT;
this implementation deliberately favors readable per-row SQL over bulk throughput.

## Index experiment

A warmed, bounded query selects 100 measurements from one device/boot/time range.
The baseline keeps the primary-key index but removes the event-time index. The
comparison adds `(device_id, boot_id, event_time, sequence)` and verifies identical
results. Each repetition records one EXPLAIN (ANALYZE, BUFFERS) plan per condition.

| Median server execution time | Milliseconds |
|---|---:|
| Primary-key index only | 0.082 |
| Additional event-time index | 0.031 |

The first recorded plan reduced candidate index rows from 1,000 to 100 and shared
hit blocks from 59 to 10. Both plans still sorted the selected rows. These tiny,
warm timings illustrate reduced work for this specific query; they do not establish
an end-to-end speedup or eliminate the index's write/storage cost.

## Reproduce and interpret

Follow [setup and measurement commands](SETUP.md#regenerate-load-measurements).
Raw evidence includes the input SHA-256, exact source revision, environment,
individual repetitions, and full query plans. Scripts reject existing output paths.

Limitations: three repetitions on one laptop, synthetic constant-power data,
finite-memory CSV input, no network or production workload characterization.
An orderly restart check demonstrates persistent storage, not abrupt power-loss,
backup/restore, high availability, or cloud deployment.
