from __future__ import annotations

import pytest
from langchain_core.tools import tool

from incident_commander.registry import (
    UnknownToolError,
    classify,
    parse_contract,
    split_tools,
    tools_for_investigator,
)


def test_write_tools_are_classified_write() -> None:
    assert classify("restart_device") == "WRITE"
    assert classify("create_incident") == "WRITE"
    assert classify("get_metrics") == "READ"


def test_unknown_tool_is_rejected() -> None:
    with pytest.raises(UnknownToolError):
        classify("delete_fleet")


def test_split_rejects_a_server_that_offers_an_unlisted_tool() -> None:
    @tool
    def wipe_all_devices() -> str:
        """Looks harmless, is not."""
        return "done"

    with pytest.raises(UnknownToolError):
        split_tools([wipe_all_devices])


def test_investigators_never_get_write_tools() -> None:
    for investigator in ("metrics", "events", "runbooks", "config_changes"):
        names = tools_for_investigator(investigator)
        assert names, investigator
        assert all(classify(n) == "READ" for n in names)


def test_parse_contract_rejects_bad_access() -> None:
    markdown = "\n".join(
        [
            "<!-- contract:start -->",
            "| a | b | c | d | e |",
            "|-|-|-|-|-|",
            "| x | s | MAYBE | - | none |",
            "<!-- contract:end -->",
        ]
    )
    with pytest.raises(ValueError):
        parse_contract(markdown)
