# Overheating (high temperature)

**Symptoms:** device temperature more than 15 C above its normal baseline, or above 60 C absolute. Often appears as a sudden step change.

**Likely causes**
- Blocked ventilation or failed enclosure fan.
- Direct sunlight on the enclosure after a site layout change.
- Firmware bug keeping the radio in high-power mode (seen on firmware 1.4.x).

**Diagnosis**
1. Check `get_metrics` for the last 6h: is the rise sudden (hardware or environment) or gradual (load)?
2. Compare vibration: a failing fan often shows a vibration change at the same time.
3. Check other devices at the same site: a site-wide rise points to ambient conditions.

**Remediation**
1. Open an incident with severity `high` if temperature is above 60 C, otherwise `medium`.
2. A restart clears a stuck radio power state; it does not fix blocked airflow.
3. Dispatch a technician if temperature stays above 60 C for more than 1 hour after restart.
