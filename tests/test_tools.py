"""Tests for the built-in tool catalog and selection helpers."""

from __future__ import annotations

from agent.tools import BUILTIN_TOOLS, TOOL_BY_NAME, default_tools, normalize_tools


def test_builtin_tool_names_are_unique():
    names = [t.name for t in BUILTIN_TOOLS]
    assert len(names) == len(set(names))


def test_tool_by_name_covers_all_tools():
    assert set(TOOL_BY_NAME) == {t.name for t in BUILTIN_TOOLS}


def test_default_tools_are_read_bash_edit_write():
    assert default_tools() == ["read", "bash", "edit", "write"]


def test_default_tools_match_flag_defaults():
    for name in default_tools():
        assert TOOL_BY_NAME[name].default is True
    for tool in BUILTIN_TOOLS:
        if tool.name not in default_tools():
            assert tool.default is False


def test_normalize_tools_lowercases_and_dedupes():
    assert normalize_tools(["Read", "read", "BASH"]) == ["read", "bash"]


def test_normalize_tools_preserves_order():
    assert normalize_tools(["grep", "read", "ls", "bash"]) == ["grep", "read", "ls", "bash"]


def test_normalize_tools_ignores_unknown_and_blank():
    assert normalize_tools(["read", "nope", "", "  ", "READ"]) == ["read"]


def test_normalize_tools_empty_input():
    assert normalize_tools([]) == []
