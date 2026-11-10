"""Validation and hashing of proposed WRITE actions.

Used in three places with the same rules: when the synthesizer proposes an
action, when an approver edits one, and in the executor right before a write.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import jsonschema

from incident_commander.registry import UnknownToolError, classify

MAX_ACTIONS = 5  # an incident never needs more; a flood of actions is a red flag


class InvalidAction(ValueError):
    pass


def args_hash(tool: str, args: dict[str, Any]) -> str:
    """sha256 over canonical JSON, so the same tool+args always hash the same."""
    canonical = json.dumps({"tool": tool, "args": args}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_write_args(tool: str, args: Any, write_schemas: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Return args if `tool` is a known WRITE tool and args match its schema, else raise."""
    try:
        access = classify(tool)
    except UnknownToolError as exc:
        raise InvalidAction(str(exc)) from exc
    if access != "WRITE":
        raise InvalidAction(f"{tool} is not a WRITE tool; only writes go through approval")
    if tool not in write_schemas:
        raise InvalidAction(f"{tool} is not offered by the connected servers")
    if not isinstance(args, dict):
        raise InvalidAction(f"{tool} args must be a JSON object")
    schema = write_schemas[tool]
    try:
        jsonschema.validate(args, schema)
    except jsonschema.ValidationError as exc:
        raise InvalidAction(f"{tool} args invalid: {exc.message}") from exc
    unknown = set(args) - set(schema.get("properties", {}))
    if unknown:
        raise InvalidAction(f"{tool} args has unknown fields {sorted(unknown)}")
    return args
