# Device restart procedure

A restart power-cycles the device remotely. It interrupts data collection for about 2 minutes.

**When a restart helps**
- Radio lock-up (connectivity loss).
- Stuck high-power radio state (some overheating cases on firmware 1.4.x).

**When a restart does not help**
- Low battery, mechanical vibration, blocked ventilation.

**Policy**
- Every restart must be approved by a human operator.
- Always state the reason for the restart.
- Never restart more than 5 devices at once without a change ticket.
