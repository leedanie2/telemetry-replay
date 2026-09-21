# Architecture

## Follow one reading

1. `telemetry/io.py` reads the CSV in arrival order, parses exact integer
   microjoules, and normalizes timezone-aware event timestamps to UTC. The entire
   input is checked before opening the database. Duplicates remain in the list.
2. `telemetry/service.py:ingest` starts one database transaction for the file.
   Device metadata must already exist. Parameterized INSERT attempts each row;
   the composite primary key is `(device_id, boot_id, sequence)`.
3. A successful INSERT increments the inserted count. On an existing key, SELECT
   reads the committed event time and energy. Matching payload is a retry;
   differing payload is a conflict. Neither event time nor energy is identity.
4. Conflicts preserve the existing measurement and record each distinct incoming
   payload in `measurement_conflicts`. Repeated delivery increments the returned
   conflict count but does not create another identical audit row.
5. Counts return after commit. If an unknown device or another database error
   aborts the batch, both new measurements and new conflict audit entries roll
   back. After an uncertain client response, replay the original file safely.
6. `sql/report.sql` partitions by device AND boot, orders by event time then
   sequence, and uses LAG for the preceding sample. Gap-spanning averages remain
   useful; a reboot boundary cannot establish total consumed energy.

## Design decisions and limits

- **Identity:** two equal counter values can be distinct samples. The same sequence
  can occur after reboot. Arrival time changes on retry. Hence all three key fields.
- **Precision:** signed BIGINT microjoules gives exact stored differences within
  its range. SQL casts to NUMERIC before subtraction/division. Output decimals are
  JSON strings to preserve precision. Microjoule storage is not sensor accuracy.
- **Time:** event time orders physics; `ingested_at` records server arrival using
  `clock_timestamp()`. Synchronization, boot identity, and no counter wrap within
  a boot are assumptions. Clock reversal appears as sequence reversal after sorting.
- **Quality:** first sample, zero duration, decreasing counter, and reversed
  sequence yield null energy/power. A gap gives an interval average, not recovered
  intermediate readings. A single quality label uses the documented CASE priority.
- **Conflicts:** the first committed measurement is retained, not declared correct.
  `has_conflicting_payload` exposes ambiguity at that sample; numerical reports
  use retained values. This flag is not propagated to the following interval.
- **Atomicity:** one file is one transaction: easy to explain and retry, but larger
  transactions retain locks longer. Count validation at the CLI is not a substitute
  for integration tests. CSV validation and SQL constraints protect different paths.
- **Concurrency:** uniqueness arbitration happens in PostgreSQL. Under default
  READ COMMITTED, the separate SELECT sees the competing committed INSERT. Service
  writes are append-only. Two batches acquiring multiple keys in opposite orders
  can deadlock; PostgreSQL aborts one, and the CLI requires replay rather than
  automatically retrying. Concurrent administrative update/delete is unsupported.
- **Index:** primary key supports identity lookup. The separate event-time index
  supports bounded time-range selection/order within a boot. Its write/storage
  cost must be justified by measured query plans. It cannot improve every query.
- **Scope:** one database, finite memory input, per-row SQL, no connection pool,
  no HTTP endpoint, no cloud or high availability. This is a tested portfolio
  service, not a production-scale architecture claim.


## Optional device filter

The CLI passes `--device` as a bound SQL parameter. NULL means all devices; otherwise the query selects the requested device. Filtering before LAG is safe here because whole device partitions are retained. A time-range filter would require separate reasoning about the predecessor outside the selected range.
