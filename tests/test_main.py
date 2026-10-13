"""Tests for the command-line entry point (agent.__main__)."""

from __future__ import annotations

import pytest

import agent.__main__ as main_module
from agent.prompts import PromptLibrary
from agent.tools import default_tools


def parse(*argv):
    return main_module.build_parser().parse_args(list(argv))


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
def test_parser_defaults():
    args = parse()
    assert args.tools is None
    assert args.select_tools is False
    assert args.model is None
    assert args.thinking is None
    assert args.cwd is None
    assert args.prompts_dir == str(main_module.DEFAULT_PROMPTS_DIR)
    assert args.session is False
    assert args.no_color is False


def test_parser_all_flags():
    args = parse(
        "-t", "read,bash",
        "--select-tools",
        "-m", "claude",
        "--thinking", "low",
        "--cwd", "/tmp/x",
        "--prompts-dir", "/tmp/prompts",
        "-s",
        "--no-color",
    )
    assert args.tools == "read,bash"
    assert args.select_tools is True
    assert args.model == "claude"
    assert args.thinking == "low"
    assert args.cwd == "/tmp/x"
    assert args.prompts_dir == "/tmp/prompts"
    assert args.session is True
    assert args.no_color is True


# --------------------------------------------------------------------------- #
# Client construction
# --------------------------------------------------------------------------- #
class StubPiClient:
    instances: list["StubPiClient"] = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.closed = False
        StubPiClient.instances.append(self)

    def start(self):
        self.started = True

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def _stub_pi_client(monkeypatch):
    StubPiClient.instances = []
    monkeypatch.setattr(main_module, "PiClient", StubPiClient)


def test_build_client_defaults():
    client = main_module.build_client(parse())
    assert client.kwargs["tools"] == default_tools()
    assert client.kwargs["model"] is None
    assert client.kwargs["thinking"] is None
    assert client.kwargs["persistent_session"] is False
    assert client.started is True


def test_build_client_tools_csv():
    client = main_module.build_client(parse("-t", "read,grep"))
    assert client.kwargs["tools"] == ["read", "grep"]


def test_build_client_tools_off():
    assert main_module.build_client(parse("-t", "off")).kwargs["tools"] == []


def test_build_client_tools_default():
    client = main_module.build_client(parse("-t", "default"))
    assert client.kwargs["tools"] == default_tools()


def test_build_client_model_thinking_cwd_session():
    args = parse("-m", "claude", "--thinking", "low", "--cwd", "/x", "-s")
    client = main_module.build_client(args)
    assert client.kwargs["model"] == "claude"
    assert client.kwargs["thinking"] == "low"
    assert client.kwargs["cwd"] == "/x"
    assert client.kwargs["persistent_session"] is True


def test_build_client_start_failure(monkeypatch, capsys):
    class FailingClient:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise main_module.PiError("nope")

    monkeypatch.setattr(main_module, "PiClient", FailingClient)
    with pytest.raises(SystemExit) as exc:
        main_module.build_client(parse())
    assert exc.value.code == 1
    assert "nope" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# Interface wiring
# --------------------------------------------------------------------------- #
class StubInterface:
    def __init__(self, client, prompts, color=None):
        self.client = client
        self.prompts = prompts
        self.color = color
        self.ran = False
        self.tool_arg = None

    def run(self):
        self.ran = True

    def _cmd_tools(self, argument):
        self.tool_arg = argument


def test_run_interface_basic(monkeypatch):
    monkeypatch.setattr(main_module, "Interface", StubInterface)
    client = object()
    interface = main_module.run_interface(client, parse())
    assert interface.client is client
    assert isinstance(interface.prompts, PromptLibrary)
    assert interface.ran is True
    assert interface.color is None


def test_run_interface_no_color(monkeypatch):
    monkeypatch.setattr(main_module, "Interface", StubInterface)
    interface = main_module.run_interface(object(), parse("--no-color"))
    assert interface.color is False


def test_run_interface_select_tools(monkeypatch):
    monkeypatch.setattr(main_module, "Interface", StubInterface)
    interface = main_module.run_interface(object(), parse("--select-tools"))
    assert interface.tool_arg == "menu"


def test_run_interface_default_prompts_dir(monkeypatch, tmp_path, capsys):
    """Without args, prompts live in the packaged prompts/ directory."""
    monkeypatch.setattr(main_module, "Interface", StubInterface)
    interface = main_module.run_interface(object(), None)
    assert interface.prompts.directory == main_module.DEFAULT_PROMPTS_DIR


# --------------------------------------------------------------------------- #
# main() wiring
# --------------------------------------------------------------------------- #
def test_main_closes_client(monkeypatch):
    class Wire:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    wire = Wire()
    seen = {}

    monkeypatch.setattr(main_module, "build_client", lambda args: wire)

    def fake_run_interface(client, args):
        seen["client"] = client

    monkeypatch.setattr(main_module, "run_interface", fake_run_interface)

    main_module.main([])
    assert seen["client"] is wire
    assert wire.closed is True


def test_module_run_as_main(fake_pi):
    """Running `python -m agent` executes the __main__ guard and main()."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    pi_dir = str(Path(fake_pi).parent)
    env = {**os.environ, "PATH": pi_dir + os.pathsep + os.environ.get("PATH", "")}
    proc = subprocess.run(
        [sys.executable, "-m", "agent", "--cwd", pi_dir],
        input="/quit\n",
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Pi interface" in proc.stdout
