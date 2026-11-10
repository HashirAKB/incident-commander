# Low battery

**Symptoms:** battery below 20%. Below 10% the device stops reporting within hours.

**Likely causes**
- Normal discharge; the device is due for a battery swap.
- Unusually fast drain caused by a poor signal (the radio retries transmissions).

**Diagnosis**
1. Use `list_devices` with `battery_below` to find all affected devices.
2. Check the drain rate over 7d with `get_metrics`. More than 0.5% per hour is abnormal.

**Remediation**
1. Open an incident with severity `medium` per affected device so field ops can schedule a swap. Use `critical` below 10%.
2. Restarting a low-battery device does **not** help and wastes charge during boot. Do not restart for low battery.
