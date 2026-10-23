# SWE-bench runs

These are checks of `pi-swebench`, run from this checkout.  They are not
benchmark scores: the first is a smoke test, the second is a six-instance
sample from SWE-bench Lite.  Both use the same fixed configuration:
`opencode-go/cat-jam-free` with thinking `max`.

- Dataset: `SWE-bench/SWE-bench_Lite` (saved to `/tmp/swe_lite.json` for the
  adapter; the harness loaded the same local file)
- Harness: `swebench==5.0.2` in a separate Python 3.13 venv
- Docker: 29.6.2, prebuilt instance images pulled from Docker Hub
- Gold control: the dataset's reference patch was resolved for the instance
  used to validate the harness.

## Lite pass (2026-10-22, two batches)

`pipeline` mode, `--skip-reproduce`, repository-scoped tool policy, the
explicit-exception prompt.  Ten tasks were chosen from three small/medium
repositories and short issue statements, so this is a sample, not a random
Lite estimate.  The pass ran in two batches (`swe-pass-3` and `swe-pass-4`).

| Instance | Patch | Resolved | Verdict detail |
|---|---|---|---|
| `pallets__flask-4992` | 63 lines | no | `test_config_from_file_toml` (`Config.from_file()` got an unexpected `text` kwarg) |
| `pallets__flask-5063` | 71 lines | no | `test_subdomain` / `test_host` (printed `Domain`, expected `Subdomain`/`Host`) |
| `psf__requests-1963` | 13 lines | **yes** | — |
| `psf__requests-2674` | 93 lines | **yes** | — |
| `psf__requests-2148` | 80 lines | **yes** | — |
| `psf__requests-2317` | 35 lines | interrupted | patch applied; test run interrupted after 23/143 tests (slow network-bound suite); not scored |
| `psf__requests-863` | 49 lines | **yes** | — |
| `pytest-dev__pytest-5221` | 30 lines | no | `test_show_fixtures` / `test_show_fixtures_verbose` |
| `pytest-dev__pytest-5495` | 24 lines | no | `test_bytes_diff_normal` / `test_bytes_diff_verbose` |
| `pytest-dev__pytest-7432` | 17 lines | **yes** | — |

Result: **5 resolved of 9 scored tasks (55.6%)**, 0 errors, 0 empty patches;
the tenth task was interrupted because its test suite was progressing too
slowly to finish the pass.  Model time was about 21 minutes total (sum of
per-instance durations); official evaluation pulled ten instance images
sequentially and took longer.

`psf__requests-2317` is excluded from the denominator.  Its harness log
records `>>>>> Applied Patch` and the test suite had started
(23 of 143 tests ran before the interrupt), but the interrupted run's
`report.json` marks `patch_successfully_applied: false` because the test
output never reached the end marker.  It is an interrupted run, not a verdict.

The failures are instructive:

- **Flask 4992** was an API-shape miss: the test calls
  `Config.from_file(..., text=...)`, while the patch invented
  `mode`/`encoding`/`errors` parameters.  The pipeline implemented a plausible
  feature rather than the requested one.
- **Flask 5063** changed the `flask routes` table to a `Domain` column, but
  the oracle expects distinct `Subdomain`/`Host` headers and exact column
  formatting.
- **pytest 5221** changed `--fixtures` output but did not match the expected
  documentation layout.
- **pytest 5495** adjusted byte-sequence repr but still missed the expected
  `test_bytes_diff_*` output.

Every completed task produced a patch that applied cleanly, and every
completed failure was a test verdict rather than an infrastructure error.

### Provider note

Two earlier attempts hit repeated
`429: Upstream request failed: Endpoint is unavailable` from
`opencode-go/cat-jam-free`, exhausted Pi's auto-retries, and were stopped.  A
one-shot probe also timed out intermittently.  After a ten-minute cooldown the
pass ran without errors.  The scored batches are `swe-pass-3` and
`swe-pass-4`; their evaluation run ids are `swe-pass-3-eval` and
`swe-pass-4-eval` / `swe-pass-4-eval-b`.

## Smoke test (2026-10-21)

A first end-to-end check of `pi-swebench`.  This is a **smoke test**, not a
benchmark score: one or two instances per mode is enough to prove the adapter,
the path policy, and the official harness work together.

## Results

| Run | Mode | Instance | Patch | Resolved |
|---|---|---|---|---|
| `flask-pipeline-1` | pipeline (reproduce included) | `pallets__flask-4045` | none captured before the reproduce turn was abandoned | — |
| `flask-pipeline-2` | pipeline, skip reproduce, no tool gate | `pallets__flask-4045` | none; stopped after diagnose searched outside the checkout | — |
| `flask-pipeline-3` | pipeline, skip reproduce, repo policy, first prompt | `pallets__flask-4045` | `assert` in `Blueprint.__init__` and `register` | no |
| `flask-agent-1` | agent, repo policy, first prompt | `pallets__flask-4045` | `assert` in `Blueprint.__init__` | no |
| `flask-pipeline-4` | pipeline, skip reproduce, repo policy, explicit-exception prompt | `pallets__flask-4045` | 29 lines: `ValueError` for name, endpoint, and view name | **yes** |
| `requests-agent-1` | agent, repo policy, first prompt | `psf__requests-3362` | 41 lines: `apparent_encoding` fallback plus a `content` guard | **yes** |
| gold control | gold | `pallets__flask-4045` | reference patch | **yes** |

The failed Flask runs had `patch_check_error: null` (the patch applied) but
the oracle's two `FAIL_TO_PASS` tests failed because they expect
`ValueError`, while the patch raised `AssertionError`.  The passing Flask run
changed exactly those validation sites.

## What the smoke test showed

1. **The harness path works.** A generated `predictions.jsonl` was accepted,
   the image was pulled, the patch applied, and the report distinguished
   resolved/unresolved/errors as expected.  The gold control resolved.
2. **A prompt is not a sandbox.** The first host run's diagnose searched
   `/home` for the fix and the reproduce role ran `find /` for dependencies.
   The repo policy (`benchmarks.policy.repo_policy`) stopped both; the model
   then used in-repository `read`/`grep`/`find`/`ls` calls.
3. **`reproduce` does not fit a host checkout.** The checkout has no project
   environment, so the role can only hunt for dependencies. `--skip-reproduce`
   lets diagnose work from the issue and source, and the official harness
   supplies the execution evidence later.
4. **Exception type matters.** SWE-bench's hidden test patch is strict. The
   first prompt let the model mirror the existing `assert`-based endpoint
   checks; adding a general rule against `assert` for input validation (raise
   `ValueError`, update related paths consistently) produced the reference
   behavior. This is an example of a prompt/quality rule changing a verdict,
   not a case-specific patch.
5. **The pipeline can win, not just the agent.** The successful Flask run was
   the full role chain: diagnose named the file and the three validation
   sites, then the gated primitive edited that file.

## Artifacts

Per-run transcripts, patches, and reports are under
`.swebench/runs/<run-id>/` (gitignored).  For the Lite pass:

```bash
cat .swebench/runs/swe-pass-3/predictions.jsonl
cat .swebench/runs/swe-pass-4/predictions.jsonl
cat .swebench/runs/swe-pass-3/instances/psf__requests-1963/patch.diff
cat .swebench/runs/swe-pass-3/logs/run_evaluation/swe-pass-3-eval/\
hms-opencode-go-cat-jam-free-max-pipeline/psf__requests-1963/report.json
```

For the resolved smoke-test run:

```bash
cat .swebench/runs/flask-pipeline-4/predictions.jsonl
cat .swebench/runs/flask-pipeline-4/instances/pallets__flask-4045/patch.diff
cat .swebench/runs/flask-pipeline-4/logs/run_evaluation/flask-eval-4/\
hms-opencode-go-cat-jam-free-max-pipeline/pallets__flask-4045/report.json
```

## Limits and next steps

- This is **not** a SWE-bench score.  A meaningful run needs the full Lite or
  Verified split, a fixed model configuration, and a run id that does not
  reuse harness caches.
- The agent runs on the host checkout.  It cannot run the project's tests and
  the shell is disabled.  The next step is a **containerized runner**: start
  the instance image, install or mount Pi and hms inside it, run the roles
  against `/testbed` with the conda environment available, and capture the
  patch from the container.  That removes the `--skip-reproduce` caveat and
  lets `reproduce` and `primitive` run tests.
- The pipeline currently opens fresh Pi sessions per pass and per primitive;
  a batch run should reuse the repository cache and bound concurrency to the
  provider and the host.
- The tool policy blocks all unknown tools.  Add any new Pi built-in to
  `benchmarks/policy.py:ALLOWED_TOOLS` deliberately, or it will be blocked.
