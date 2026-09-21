WITH ordered AS (
    SELECT m.*,
           lag(sequence) OVER samples AS previous_sequence,
           lag(event_time) OVER samples AS previous_time,
           lag(energy_uj) OVER samples AS previous_energy_uj
    FROM measurements AS m
    WHERE %(device_id)s::text IS NULL OR m.device_id = %(device_id)s
    WINDOW samples AS (
        PARTITION BY device_id, boot_id ORDER BY event_time, sequence
    )
), intervals AS (
    SELECT *, extract(epoch FROM event_time - previous_time) AS duration_s,
           energy_uj::numeric - previous_energy_uj::numeric AS delta_uj,
           CASE
               WHEN previous_sequence IS NULL THEN 'first_sample'
               WHEN event_time = previous_time THEN 'zero_duration'
               WHEN energy_uj < previous_energy_uj THEN 'decreasing_counter'
               WHEN sequence <= previous_sequence THEN 'sequence_reversal'
               WHEN sequence - previous_sequence > 1 THEN 'gap'
               ELSE 'ok'
           END AS quality
    FROM ordered
)
SELECT device_id, boot_id, sequence, event_time, previous_sequence, duration_s,
       CASE WHEN quality IN ('ok', 'gap') THEN delta_uj / 1000000 END AS energy_j,
       CASE WHEN quality IN ('ok', 'gap')
            THEN delta_uj / 1000000 / nullif(duration_s, 0) END AS average_power_w,
       quality,
       EXISTS (
           SELECT 1 FROM measurement_conflicts AS c
           WHERE (c.device_id, c.boot_id, c.sequence) =
                 (intervals.device_id, intervals.boot_id, intervals.sequence)
       ) AS has_conflicting_payload
FROM intervals
ORDER BY device_id, boot_id, event_time, sequence;
