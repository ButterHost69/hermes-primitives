"""Tests for replaying saved SWE-bench transcripts."""

from __future__ import annotations

import json
import sys
import time

import pytest

import benchmarks.cli as cli_module
import benchmarks.replay as replay_module
from benchmarks.replay import (
    ReplayError,
    load_records,
    replay_main,
    select_replay,
)
from tui.replay import ReplayApp, is_text_delta


def write_transcript(path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(record) for record in records) + "\n",
        encoding="utf-8",
    )


def visual_records():
    return [
        {"type": "primitives_fanned_out", "files": ["a.py", "b.py"], "pass": 1},
        {
            "type": "message_update",
            "primitive": "a.py",
            "assistantMessageEvent": {"type": "text_delta", "delta": "hello"},
        },
        {
            "type": "tool_execution_start",
            "primitive": "a.py",
            "toolName": "read",
            "args": {"path": "a.py"},
        },
        {
            "type": "tool_execution_end",
            "primitive": "a.py",
            "toolName": "read",
            "isError": False,
            "durationMs": 1,
        },
        {"type": "primitive_reported", "primitive": "a.py", "report": "{}"},
    ]


def make_run(tmp_path, names=("a__b-1",)):
    run = tmp_path / "run"
    for name in names:
        instance = run / "instances" / name
        write_transcript(instance / "transcript.jsonl", visual_records())
        (instance / "result.json").write_text(
            json.dumps({"model": "model-a", "thinking": "max", "mode": "pipeline"}),
            encoding="utf-8",
        )
    (run / "summary.json").write_text(
        json.dumps({"model": "summary-model", "thinking": "low", "mode": "pipeline"}),
        encoding="utf-8",
    )
    return run


# --------------------------------------------------------------------------- #
# Loading and selection
# --------------------------------------------------------------------------- #
def test_load_records_keeps_visual_records_only(tmp_path):
    records = visual_records() + [
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "thinking_delta", "delta": "hmm"},
        },
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "toolcall_delta", "delta": "{"},
        },
        {"type": "extension_ui_request", "method": "input"},
        {"type": "message_end", "message": {"role": "assistant"}},
    ]
    path = tmp_path / "transcript.jsonl"
    write_transcript(path, records)

    loaded = load_records(path)
    assert [record["type"] for record in loaded] == [
        "primitives_fanned_out",
        "message_update",
        "tool_execution_start",
        "tool_execution_end",
        "primitive_reported",
        "message_update",
    ]
    assert loaded[5]["assistantMessageEvent"]["type"] == "thinking_delta"


def test_load_records_can_drop_thinking(tmp_path):
    path = tmp_path / "transcript.jsonl"
    write_transcript(
        path,
        [
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "thinking_delta", "delta": "hmm"},
            },
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "hi"},
            },
        ],
    )
    loaded = load_records(path, show_thinking=False)
    assert [record["assistantMessageEvent"]["type"] for record in loaded] == [
        "text_delta"
    ]


def test_load_records_can_keep_the_raw_stream(tmp_path):
    path = tmp_path / "transcript.jsonl"
    write_transcript(
        path,
        visual_records()
        + [{"type": "extension_ui_request", "method": "input"}],
    )
    assert len(load_records(path, include_all=True)) == 6


def test_load_records_rejects_bad_json(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text('{"type": "ok"}\nnot-json\n', encoding="utf-8")
    with pytest.raises(ReplayError, match="invalid JSON"):
        load_records(path)


def test_load_records_rejects_an_empty_replay(tmp_path):
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        json.dumps({"type": "extension_ui_request"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(ReplayError, match="no replayable records"):
        load_records(path)


def test_select_replay_resolves_a_single_instance(tmp_path):
    run = make_run(tmp_path)
    selection = select_replay(run)
    assert selection.instance_id == "a__b-1"
    assert selection.transcript == run / "instances" / "a__b-1" / "transcript.jsonl"
    # result.json is more specific than the run summary
    assert selection.model == "model-a"
    assert selection.thinking == "max"


def test_select_replay_accepts_instance_dir_and_transcript_file(tmp_path):
    run = make_run(tmp_path)
    instance = run / "instances" / "a__b-1"
    assert select_replay(instance).instance_id == "a__b-1"
    assert select_replay(instance / "transcript.jsonl").instance_id == "a__b-1"


def test_select_replay_requires_an_instance_when_there_are_several(tmp_path):
    run = make_run(tmp_path, names=("a__b-1", "a__b-2"))
    with pytest.raises(ReplayError, match="pass --instance"):
        select_replay(run)
    assert select_replay(run, instance="a__b-2").instance_id == "a__b-2"
    with pytest.raises(ReplayError, match="no instance"):
        select_replay(run, instance="missing")


def test_select_replay_rejects_an_unsafe_instance_id(tmp_path):
    run = make_run(tmp_path)
    with pytest.raises(ReplayError, match="invalid instance id"):
        select_replay(run, instance="../escape")


def test_select_replay_rejects_a_missing_path(tmp_path):
    with pytest.raises(ReplayError, match="no such run"):
        select_replay(tmp_path / "missing")


def test_select_replay_rejects_a_run_without_transcripts(tmp_path):
    run = tmp_path / "run"
    (run / "instances").mkdir(parents=True)
    with pytest.raises(ReplayError, match="no instance transcripts"):
        select_replay(run)


# --------------------------------------------------------------------------- #
# ReplayApp
# --------------------------------------------------------------------------- #
def test_replay_app_steps_and_rebuilds():
    app = ReplayApp(visual_records(), color=False, paused=True, speed=10)
    assert app._position == 0
    assert app.feed.order == []

    app._step_forward()
    assert app._position == 1
    assert app.feed.order == ["a.py", "b.py"]
    assert app.feed.status["a.py"] == "running"

    app._step_forward(2)  # fanned-out record + text delta + tool start
    assert app._position == 3

    app._step_back()
    assert app._position == 2

    app._jump_end()
    assert app._position == len(app.records)
    assert app.feed.status == {"a.py": "done", "b.py": "done"}

    app._jump_start()
    assert app._position == 0
    assert app.feed.order == []


def test_replay_app_next_event_skips_text_deltas():
    app = ReplayApp(visual_records(), color=False, paused=True)
    app._next_event()
    assert app._position == 1  # fanned out is a real event
    app._next_event()
    assert app._position == 3  # skips the text delta, stops on the tool call
    app._next_event()
    assert app._position == 4


def test_replay_app_key_handling_and_frame():
    app = ReplayApp(visual_records(), color=False, paused=True, speed=10)
    app._jump_end()
    app._handle_key("\t")
    assert app.selected in app.feed.order
    app._handle_key("\r")
    assert app.focus is not None
    app._handle_key("ESC")
    assert app.focus is None

    app._handle_key("[")
    assert app.speed == 5
    app._handle_key("]")
    assert app.speed == 10
    app._handle_key(" ")
    assert app.playing is True
    app._handle_key(" ")
    assert app.playing is False
    app._handle_key("g")
    assert app._position == 0
    app._handle_key("G")
    assert app._position == len(app.records)

    lines, _ = app._build_frame()
    assert any("hms replay" in line for line in lines)
    assert "replay" in app._status_content()
    assert "Space play/pause" in app._phase_hint()

    app._handle_key("q")
    assert app.done is True


def test_replay_app_advance_feeds_records():
    app = ReplayApp(visual_records(), color=False, paused=False, speed=100)
    assert app.playing is True
    app._last_time = time.monotonic() - 0.2
    app._advance()
    assert app._position > 0
    assert app.feed.order == ["a.py", "b.py"]


def test_replay_app_starts_at_the_end_when_requested():
    app = ReplayApp(visual_records(), color=False, start="end")
    assert app._position == len(app.records)
    assert app.playing is False
    assert app.feed.status == {"a.py": "done", "b.py": "done"}


def test_replay_app_covers_status_hints_and_boundaries():
    app = ReplayApp(
        visual_records(),
        color=False,
        paused=True,
        instance="a__b-1",
        model="m",
        thinking="max",
    )

    assert "a__b-1" in app._header_content()
    assert "paused" in app._status_content()
    assert "Space play/pause" in app._phase_hint()
    assert "Tab switch" not in app._phase_hint()

    app.playing = True
    assert "playing" in app._status_content()
    app.playing = False

    app._step_forward()
    assert "primitives" in app._status_content()
    assert "Tab switch" in app._phase_hint()

    app._handle_key("\r")
    assert app.focus is not None
    assert "every primitive" in app._phase_hint()
    app._handle_key("ESC")

    app._handle_key("UP")
    app._handle_key("c")
    app._handle_key("HOME")

    app._handle_key("RIGHT")
    app._handle_key("LEFT")
    app._handle_key("n")
    app._handle_key("r")
    app._handle_key("p")
    app._handle_key("P")
    app._handle_key("g")
    app._handle_key("G")
    app._handle_key("]")
    app._handle_key("[")

    app._rebuild(len(app.records))
    assert app._position == len(app.records)
    app._set_position(0)
    assert app._position == 0

    app._jump_end()
    app.playing = True
    app._advance()
    assert app.playing is False
    app._next_event()
    assert app._position == len(app.records)


def test_replay_app_advance_no_records_due_and_partial():
    app = ReplayApp(visual_records(), color=False, paused=False, speed=1)
    app._last_time = time.monotonic()
    app._advance()  # not enough elapsed time for a record
    assert app._position == 0

    app._set_speed(20)
    app._last_time = time.monotonic() - 0.2
    app._advance()
    assert 0 < app._position < len(app.records)
    assert app.playing is True


def test_replay_app_handles_ctrl_c_and_ignores_activity_when_paused():
    app = ReplayApp(visual_records(), color=False, paused=True)
    app._advance()
    assert app._position == 0
    app._handle_key("\x03")
    assert app.done is True


def test_replay_app_run_loop_with_a_fake_terminal():
    class FakeTerminal:
        def __init__(self):
            self.entered = False
            self.exited = False
            self.painted = False

        def enter(self):
            self.entered = True

        def exit(self):
            self.exited = True

        def paint(self, lines, cursor=None):
            self.painted = True

        def size(self):
            return (100, 30)

        def read_key(self, timeout=0.05):
            return self.keys.pop(0)

    app = ReplayApp(visual_records(), color=False, paused=False)
    terminal = FakeTerminal()
    terminal.keys = [None, "q"]
    app.term = terminal

    assert app.run() == {}
    assert terminal.entered is True
    assert terminal.exited is True
    assert terminal.painted is True


def test_replay_app_run_loop_when_paused():
    class FakeTerminal:
        def enter(self):
            pass

        def exit(self):
            pass

        def paint(self, lines, cursor=None):
            pass

        def size(self):
            return (100, 30)

        def read_key(self, timeout=0.05):
            return "q"

    app = ReplayApp(visual_records(), color=False, paused=True)
    app.term = FakeTerminal()
    assert app.run() == {}


def test_is_text_delta():
    assert is_text_delta(
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "text_delta"},
        }
    )
    assert not is_text_delta(
        {
            "type": "message_update",
            "assistantMessageEvent": {"type": "toolcall_delta"},
        }
    )
    assert not is_text_delta({"type": "message_end"})


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_replay_main_requires_a_terminal(tmp_path, monkeypatch, capsys):
    run = make_run(tmp_path)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert replay_main([str(run)]) == 2
    assert "interactive terminal" in capsys.readouterr().err


def test_replay_main_help():
    with pytest.raises(SystemExit) as exc:
        replay_main(["--help"])
    assert exc.value.code == 0


def test_cli_dispatches_replay(monkeypatch):
    monkeypatch.setattr(replay_module, "replay_main", lambda argv: 7)
    assert cli_module.main(["replay", "some-run"]) == 7
