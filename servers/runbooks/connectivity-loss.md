# Connectivity loss (weak signal)

**Symptoms:** signal strength below -100 dBm, or a sudden drop of more than 20 dB.

**Likely causes**
- Antenna damaged or disconnected.
- Gateway outage at the site (several devices drop together).
- Radio firmware lock-up.

**Diagnosis**
1. Check other devices at the same site with `list_devices`. If only one device is affected, suspect the device.
2. Use `get_metrics` with window `6h` to see when the drop started.

**Remediation**
1. A restart often recovers a radio firmware lock-up. Request a restart (requires human approval).
2. Open an incident with severity `medium` if the signal does not recover within 30 minutes of a restart.
