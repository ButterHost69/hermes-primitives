# SWE-bench adapter

`pi-swebench` runs the hms roles over a SWE-bench task, captures the resulting
tree as a patch, and (optionally) hands that patch to the official SWE-bench
harness.  It is deliberately thin: SWE-bench already owns the datasets, the
per-project environments, and the scoring, so the adapter only supplies the
thing hms knows how to make — a patched checkout.

## What it does

For each selected task:

1. Download the base commit's GitHub tarball and commit it as a local
   baseline (no full clone, no repository history).
2. Run the selected runner in that checkout.
3. Diff the tree against the baseline, including new source files and
   excluding agent artifacts (`.sessions/`, `__pycache__/`, caches).
4. Check that the patch applies to a detached baseline worktree.
5. Write one `predictions.jsonl` row per task.

The patch is what SWE-bench grades.  The agent does **not** run the project's
tests: the host checkout has none of the project's dependencies, and the
official harness runs the oracle tests in the project's own image.

## Setup

The hms environment needs only the standard library for the adapter.  The
official harness needs `datasets` and a Docker client, and its dependency set
is easier to keep in its own environment:

```bash
cd /path/to/hms
uv venv --python 3.13 .swebench/swebench-venv
uv pip install --python .swebench/swebench-venv/bin/python "swebench==5.0.2"

docker info                       # the evaluation phase uses Docker
```

One prebuilt SWE-bench instance image is roughly 1–2 GB compressed and about
4 GB on disk; the harness recommends 120 GB of free disk for a large run.  For
a single task, 5–10 GB is comfortable.  Evaluation pulls the image named by
the dataset row (the `image` column), so no local image build is required.

## Quick start

Inspect the selection without calling a model:

```bash
.venv/bin/python -m benchmarks.swebench \
    --instance-id pallets__flask-4045 --dry-run
```

Run the project's role chain on one task:

```bash
.venv/bin/python -m benchmarks.swebench \
    --instance-id pallets__flask-4045 \
    --mode pipeline \
    --model opencode-go/cat-jam-free \
    --thinking max \
    --run-id flask-pipeline-1 \
    --skip-reproduce \
    --keep-worktrees
```

`--skip-reproduce` is recommended for host checkouts: the reproduce role has
`bash`, but a checkout without the project's dependencies has nothing to run,
so that turn can be spent searching the machine.  Diagnose can locate the
responsible code from the issue alone.

Generate and grade in one command:

```bash
.venv/bin/python -m benchmarks.swebench \
    --instance-id pallets__flask-4045 \
    --mode pipeline \
    --evaluate \
    --swebench-bin .swebench/swebench-venv/bin/swebench
```

Run the plain-agent baseline instead:

```bash
.venv/bin/python -m benchmarks.swebench \
    --instance-id pallets__flask-4045 --mode agent --evaluate \
    --swebench-bin .swebench/swebench-venv/bin/swebench
```

The CLI defaults to `SWE-bench/SWE-bench_Lite`, the
`opencode-go/cat-jam-free` model, `max` reasoning, and the `pipeline` mode.

## Modes

| Mode | What runs | Best for |
|---|---|---|
| `pipeline` | `reproduce` → `diagnose` → `primitive_manager`, with the primitive write gate | Exercising hms itself; multi-file reasoning |
| `agent` | One `PiSession` with every built-in tool and a patch-oriented prompt | A baseline or fallback when the role chain cannot parse a task |

Both modes use a repository-relative, source-only policy in the prompt: no
test edits, no package installs, no network.  The pipeline's primitive gate
only fences `write`/`edit` to the diagnosed artifact; the shell is not fenced
by `writes_only_to()`.

The adapter also gates every tool call with `benchmarks.policy.repo_policy()`:
file paths outside the checkout are blocked, and in host mode the shell,
`powershell`, and `codemode` are disabled.  Prompts alone did not hold — in the
first host run, diagnose searched `/home` for the fix — so the policy is the
enforcement point, not the prompt.  `--allow-shell` opts the shell back in for
a future containerized run.

## Output

```
.swebench/
├── cache/
│   ├── datasets/<dataset>.<split>.json
│   └── tarballs/<instance>@<commit>.tar.gz
└── runs/<run-id>/
    ├── predictions.jsonl             # official harness input
    ├── summary.json
    ├── instances/<instance>/
    │   ├── transcript.jsonl          # every streamed Pi record
    │   ├── patch.diff
    │   └── result.json               # role results, timing, errors
    └── logs/run_evaluation/...       # written by the official harness
```

`--resume` keeps a non-empty prediction instead of rerunning that task;
empty or failed predictions are retried.  `--refresh` re-reads the dataset and
re-materializes the checkout.

## Harness invocation

`--evaluate` shells out to:

```bash
swebench eval SWE-bench/SWE-bench_Lite \
    --predictions <run-dir>/predictions.jsonl \
    --run-id <run-id> \
    --instance <id> ... \
    --workers 1 \
    --report-dir <run-dir>
```

The harness caches results by `run-id` + instance.  A fresh patch needs a
fresh `--eval-run-id` (or a new run id).  The result report is
`<run-dir>/<model>.<eval-run-id>.json`; per-instance logs and
`report.json` live under `<run-dir>/logs/run_evaluation/`.

To evaluate by hand:

```bash
cd .swebench/runs/<run-id>
/path/to/.swebench/swebench-venv/bin/swebench eval SWE-bench/SWE-bench_Lite \
    -p predictions.jsonl --run-id eval-1 -j 1
```

## Replay

Every run writes one `transcript.jsonl` per task.  Replay one without a model,
Docker, or network access:

```bash
# Single-task run: the task is implied
.venv/bin/pi-swebench replay .swebench/runs/swe-pass-4/instances/psf__requests-2148

# Multi-task run: choose the task
.venv/bin/pi-swebench replay .swebench/runs/swe-pass-3 \
    --instance pallets__flask-4992

# Start with the final state already built, then inspect/scroll
.venv/bin/pi-swebench replay .swebench/runs/swe-pass-4 \
    --instance psf__requests-2148 --start end --paused
```

The replay uses the live TUI's renderer, so the primitive panel, per-file
streams, tool activity, and gate blocks are rebuilt from the transcript.  It
is read-only and starts at the first record playing at 60 records/s by default.

| Key | Action |
|---|---|
| `Space` / `p` | play/pause |
| `→` / `←` | step one record forward/back |
| `n` | jump to the next non-text event (tool, report, fan-out) |
| `g` / `G` | jump to the start / final state |
| `r` | restart from the beginning and play |
| `[` / `]` | halve / double playback speed |
| `Tab` | move along the primitives of a fan-out |
| `Enter` | open the highlighted primitive |
| `Esc` / `q` / `Backspace` | leave a primitive |
| `↑`/`↓`, `PgUp`/`PgDn`, `Home`/`End` | scroll |
| `q` | quit the replay |

`--all-events` replays the raw stream instead of the visually meaningful
records; `--no-thinking` drops thinking deltas; `--theme light` switches the
palette.  The panel's elapsed times are replay-relative, not the original
wall-clock times.

## What the runs showed

A first end-to-end check on `pallets__flask-4045` and `psf__requests-3362`
resolved both instances once the repo policy was in place; the Flask task also
needed an explicit-exception prompt rule.  A later 10-task Lite sample from
three repositories scored 5 resolved of 9 completed tasks, with one
interrupted.  The earlier attempts, the failing assertion-vs-`ValueError`
case, the interrupted run, and the harness gold control are recorded in
[RESULTS.md](RESULTS.md).

## Limits

- **Host checkout, no project environment.**  `reproduce` may be unable to run
  the project's tests, and the primitive's `bash` commands may fail on missing
  dependencies.  The patch is still generated from the source.  A future
  containerized runner should execute hms inside the SWE-bench image so the
  conda environment and the oracle test command are available.
- **Patch only.**  The adapter does not run `FAIL_TO_PASS` or `PASS_TO_PASS`;
  the official harness owns that judgement.
- **The primitive gate is per diagnosed file.**  In pipeline mode a task that
  needs a new file, or an edit to a file diagnose did not name, is blocked by
  `writes_only_to()`.  `--mode agent` is the fallback for those tasks.
- **No test edits are graded.**  The oracle supplies its own test patch; an
  agent that edits tests is not rewarded, and the prompt forbids it.
- **GitHub codeload is unauthenticated.**  A large run can hit rate limits;
  use a token, a local mirror, or a pre-populated `.swebench/cache/tarballs/`
  for a full split.
- **Docker and provider cost.**  Each evaluation pulls the instance image;
  each pipeline run makes several model turns.  Start with one task and
  `--eval-workers 1`, then scale out.

## Cleanup

```bash
docker image prune -f
rm -rf .swebench/runs/<run-id>
```

Keep `.swebench/cache/datasets/` if you plan to rerun; it avoids a repeated
Hugging Face round trip.
