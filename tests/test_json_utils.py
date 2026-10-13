"""Tests for the JSON validation helpers."""

from __future__ import annotations

from utils.json_utils import JsonCheck, verify_json


def test_verify_json_valid_object():
    check = verify_json('{"a": 1}')
    assert isinstance(check, JsonCheck)
    assert check.ok is True
    assert check.value == {"a": 1}
    assert check.error is None


def test_verify_json_valid_with_surrounding_whitespace():
    check = verify_json("  [1, 2, 3]\n")
    assert check.ok is True
    assert check.value == [1, 2, 3]


def test_verify_json_null_is_valid():
    check = verify_json("null")
    assert check.ok is True
    assert check.value is None
    assert check.error is None


def test_verify_json_scalar():
    check = verify_json('"just a string"')
    assert check.ok is True
    assert check.value == "just a string"


def test_verify_json_invalid_returns_error():
    check = verify_json("not json")
    assert check.ok is False
    assert check.value is None
    assert check.error is not None


def test_verify_json_non_string_input():
    check = verify_json(None)  # type: ignore[arg-type]
    assert check.ok is False
    assert check.error is not None
