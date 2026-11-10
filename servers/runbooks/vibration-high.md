# High or rising vibration

**Symptoms:** vibration RMS rising steadily over hours (drift) or above 5 mm/s.

**Likely causes**
- Bearing wear on the monitored motor (gradual drift over hours or days).
- Loose mounting bracket (sudden step change).

**Diagnosis**
1. Use `detect_anomalies` with method `ewma`: it is designed to catch slow drifts that a point-by-point z-score can miss.
2. Use `get_metrics` with window `24h` to see the trend.

**Remediation**
1. Open an incident with severity `high`; mechanical failure can follow within days.
2. A restart does not change mechanical vibration. Do not restart.
