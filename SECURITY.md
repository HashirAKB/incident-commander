# Security

This is a portfolio project that controls simulated devices with synthetic data. It is built as if the
devices were real, and this file describes the model.

## Threat model (summary)

Assets: device availability (`restart_device` is destructive), the incident tracker, the approver token,
the model API key.

| threat | mitigation | proof |
| --- | --- | --- |
| The model writes without approval | WRITE tools are bound only to the executor node; no model is ever bound to them | `tests/test_structural_safety.py` |
| Prompt injection in tool output | Tool output is data in a JSON block; decisions come only from the resume payload; a hijacked model can only propose actions, which are validated and shown to a human | injection fixtures in `servers/seed.py`, eval cases `i01` to `i04`, `p23` to `p25` |
| Stale, replayed or tampered approval | Decisions are bound to thread, approval id, action id and args hash; refused with HTTP 409 / CLI error | `tests/test_approval.py`, `tests/test_api.py` |
| Approver widens scope | Edited args are re-validated with the same schema; unknown fields, a different tool or new actions are refused | `tests/test_approval.py` |
| Silence treated as consent | A missing decision is a reject; an unanswered approval expires after `APPROVAL_TTL` (default 4h) and expiry is a reject | `tests/test_approval_flow.py` |
| Double execution after a crash | Write ledger keyed by (thread, action, args hash). At-least-once with dedupe: a crash after the server performed a write and before the ledger insert commits can repeat that one write | `docs/adr/0004-ledger-dedupe.md` |
| Fork or replay used to fake approval | Forks may only change triage, findings or diagnosis; new actions always need a new approval | `tests/test_timetravel.py` |
| Path traversal | thread_id must be a canonical UUID before any file path is built | `tests/test_paths.py` |
| Secret leakage | Keys never enter state; span attributes are redacted; MCP child processes get only the variables they need | `tests/test_tracing.py` |
| Unauthenticated approval | Bearer token (`APPROVER_TOKEN`, constant-time compare) on `POST /runs` and `POST /runs/{id}/decisions`; without a token the API is read-only and refuses to bind anything but 127.0.0.1 | `tests/test_api.py` |

## Approval authentication

One shared bearer token for all approvers. There are no user accounts, so the audit trail records the
decision, not the person. Run one API process per checkpointer: decisions are serialized per thread inside
a process, but two processes answering the same approval at the same instant are not coordinated.

## Injection handling

Untrusted text (runbooks, device events, change notes) reaches models only as data. The prompts say so,
but the guarantee does not depend on the prompt: even a model that follows the injected text cannot call
a write tool and cannot approve anything. The approval page inserts all run content with `textContent`.

## Reporting a vulnerability

Please open a GitHub issue without exploit details and ask for a private channel, or use GitHub's private
vulnerability reporting on this repository. I aim to answer within a week.
