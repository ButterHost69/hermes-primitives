# hms

A simple terminal interface for interacting with the [Pi](https://github.com/earendil-works/pi) coding agent.

## What it does

`python -m agent` launches Pi in RPC mode and wraps it in a small REPL that:

- streams the assistant's response and tool activity to your terminal,
- lets you **insert custom prompts** (a saved-prompt library with `/prompt`),
- lets you **select tools** with an interactive menu (`/tools menu`) or inline (`/tools read,bash,edit,write`),
- gives quick controls for the model, reasoning level, session, and more.

## Usage

```sh
python -m agent                      # start with default tools
python -m agent --select-tools       # pick tools in a menu first
python -m agent -t read,bash,grep    # explicit tool allowlist
python -m agent -m claude            # start on a specific model
python -m agent --thinking low
python -m agent --cwd /path/to/project
```

Sessions persist automatically under `.sessions/` in the working directory.

## Controls

| Command | Action |
|---|---|
| `/tools [menu\|<csv>\|default\|off]` | Select tools (interactive menu, or explicit list) |
| `/models` | List available models |
| `/model <pattern>` | Switch model by id/name substring |
| `/thinking <level>` | Set reasoning level |
| `/state` | Show session state |
| `/new` | Start a fresh session |
| `/abort` | Stop the running operation (or Ctrl+C) |
| `/prompts` | List saved custom prompts |
| `/prompt add <name>` | Save a new custom prompt (multi-line, end with `.`) |
| `/prompt <name>` | Insert a saved prompt into the input line |
| `/prompt show <name>` | Print a saved prompt |
| `/prompt del <name>` | Delete a saved prompt |
| `/cwd` | Show the working directory |
| `/clear` | Clear the screen |
| `/help` | Show help |
| `/quit` | Exit |

Custom prompts are plain Markdown files stored in `agent/prompts/`, so you can
edit them outside the interface too.

## Layout

```
agent/
├── __main__.py     # CLI entry point (python -m agent)
├── interface.py    # interactive REPL and command controls
├── client.py       # JSONL RPC client for the Pi subprocess
├── session.py      # PiSession: owns its PiClient, synchronous request/response
├── prompts.py      # custom prompt library + load_prompt()
├── tools.py        # built-in tool catalog and selection
└── prompts/        # your saved prompts (.md files)

roles/
├── triage.py       # classify a message into a JSON verdict (write tools disabled)
├── diagnose.py     # diagnose an error trace into JSON findings + fixes (write tools disabled)
└── prompts/
    ├── triage.md   # the triage prompt template
    └── diagnose.md # the diagnose prompt template

utils/
├── json_utils.py   # verify_json(): check model output is valid JSON
```

Requires the `pi` executable on `PATH` and Python 3.9+.

## Roles

Roles are single-purpose, model-driven tasks built on the agent package:

```sh
pi-triage "the login button does nothing"
pi-triage --model claude < report.txt
```

`triage` runs Pi with write-capable tools (`write`, `edit`, `bash`) disabled,
loads its prompt from `roles/prompts/triage.md`, and asks the model to return a
JSON verdict.  If the response is not valid JSON, it retries once in the same
session before giving up.

```sh
pi-diagnose "$trace"
pi-diagnose --model claude --cwd /path/to/project < traceback.txt
```

`diagnose` runs Pi with write-capable tools disabled, loads its prompt from
`roles/prompts/diagnose.md`, and asks the model to inspect the working tree
and return a JSON object listing the files responsible for the error together
with possible fixes for each.  If the response is not valid JSON, it retries
once in the same session before giving up.
