-- AI-assisted implementation; see docs/ARCHITECTURE.md for review exercises.
CREATE TABLE devices (
    device_id TEXT PRIMARY KEY CHECK (length(trim(device_id)) > 0),
    label TEXT NOT NULL CHECK (length(trim(label)) > 0)
);

CREATE TABLE measurements (
    device_id TEXT NOT NULL REFERENCES devices(device_id),
    boot_id TEXT NOT NULL CHECK (length(trim(boot_id)) > 0),
    sequence BIGINT NOT NULL CHECK (sequence >= 0),
    event_time TIMESTAMPTZ NOT NULL CHECK (isfinite(event_time)),
    energy_uj BIGINT NOT NULL CHECK (energy_uj >= 0),
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (device_id, boot_id, sequence)
);

-- Retain each distinct conflicting payload once; replay counters count deliveries.
CREATE TABLE measurement_conflicts (
    device_id TEXT NOT NULL,
    boot_id TEXT NOT NULL,
    sequence BIGINT NOT NULL,
    incoming_event_time TIMESTAMPTZ NOT NULL CHECK (isfinite(incoming_event_time)),
    incoming_energy_uj BIGINT NOT NULL CHECK (incoming_energy_uj >= 0),
    detected_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (device_id, boot_id, sequence, incoming_event_time, incoming_energy_uj),
    FOREIGN KEY (device_id, boot_id, sequence)
        REFERENCES measurements(device_id, boot_id, sequence)
);

-- Supports event-ordered windows and bounded event-time lookups within a boot.
CREATE INDEX measurements_event_order
    ON measurements (device_id, boot_id, event_time, sequence);
