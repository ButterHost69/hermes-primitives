"""Tests for the custom prompt library."""

from __future__ import annotations

import pytest

from agent.prompts import PromptLibrary, load_prompt


def test_load_prompt_reads_markdown_file(tmp_path):
    path = tmp_path / "instructions.md"
    path.write_text("  Do the thing.\n\n", encoding="utf-8")
    assert load_prompt(path) == "Do the thing."


def test_load_prompt_accepts_str_path(tmp_path):
    path = tmp_path / "instructions.md"
    path.write_text("hello", encoding="utf-8")
    assert load_prompt(str(path)) == "hello"


def test_load_prompt_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_prompt(tmp_path / "nope.md")


@pytest.fixture
def library(tmp_path):
    return PromptLibrary(tmp_path / "prompts")


def test_directory_is_created(library):
    assert library.directory.exists()


def test_list_is_sorted(library):
    library.save("zebra", "z")
    library.save("alpha", "a")
    library.save("mike", "m")
    assert library.list() == ["alpha", "mike", "zebra"]


def test_get_missing_returns_none(library):
    assert library.get("missing") is None


def test_save_strips_and_adds_newline(library):
    library.save("p", "  some text  \n\n")
    assert library.get("p") == "some text\n"


def test_delete_existing(library):
    library.save("p", "text")
    assert library.delete("p") is True
    assert library.get("p") is None


def test_delete_missing(library):
    assert library.delete("nope") is False


def test_filename_sanitization(library):
    library.save("my cool prompt!", "hi")
    assert (library.directory / "my-cool-prompt.md").exists()


def test_filename_sanitization_all_special(library):
    library.save("!!!", "hi")
    assert (library.directory / "prompt.md").exists()


def test_resolve_exact(library):
    library.save("review", "r")
    assert library.resolve("review") == ("review", "r\n")


def test_resolve_unique_prefix(library):
    library.save("review-code", "r")
    assert library.resolve("review") == ("review-code", "r\n")


def test_resolve_unique_contains(library):
    library.save("my-review-helper", "r")
    assert library.resolve("review") == ("my-review-helper", "r\n")


def test_resolve_ambiguous_returns_none(library):
    library.save("review-one", "1")
    library.save("review-two", "2")
    assert library.resolve("review") is None


def test_resolve_missing_returns_none(library):
    assert library.resolve("missing") is None
