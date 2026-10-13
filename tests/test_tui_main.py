"""Tests for the TUI command-line entry point."""

from __future__ import annotations

import tui.__main__ as main_module
from tui.__main__ import build_parser, main


def test_build_parser_defaults():
    args = build_parser().parse_args(["repo"])
    assert args.codebase == "repo"
    assert args.model == "claude"
    assert args.thinking == "low"
    assert args.theme == "dark"
    assert args.no_thinking is False
    assert args.no_color is False


def test_build_parser_flags():
    args = build_parser().parse_args(
        [
            "repo",
            "-m",
            "claude-2",
            "--thinking",
            "high",
            "--api-key",
            "secret",
            "--theme",
            "light",
            "--no-thinking",
            "--no-color",
        ]
    )
    assert args.model == "claude-2"
    assert args.thinking == "high"
    assert args.api_key == "secret"
    assert args.theme == "light"
    assert args.no_thinking is True
    assert args.no_color is True


def test_main_wires_pipeline_and_app(monkeypatch, capsys):
    captured = {}

    class FakePipeline:
        def __init__(self, codebase, model, thinking, api_key):
            captured["pipeline"] = (codebase, model, thinking, api_key)

    class FakeApp:
        def __init__(self, pipeline, theme=None, color=None, show_thinking=True):
            captured["app"] = (theme, color, show_thinking)

        def run(self):
            return {"reproduce": {"ok": True}}

    monkeypatch.setattr(main_module, "Pipeline", FakePipeline)
    monkeypatch.setattr(main_module, "PipelineApp", FakeApp)

    main(["repo", "--no-color", "--no-thinking"])

    out = capsys.readouterr().out
    assert "reproduce" in out
    assert captured["pipeline"][0] == "repo"
    assert captured["app"][1] is False  # color=False
    assert captured["app"][2] is False  # show_thinking=False
