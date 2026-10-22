# SWE-bench smoke test

A first end-to-end check of `pi-swebench`, run on 2026-10-21 from this checkout.
This is a **smoke test**, not a benchmark score: one or two instances per mode
is enough to prove the adapter, the path policy, and the official harness work
together.

- Model: `opencode-go/cat-jam-free`, thinking `max`
- Dataset: `SWE-bench/SWE-bench_Lite` (saved to `/tmp/swe_lite.json` for the
  adapter; the harness loaded the Hugging Face id)
- Harness: `swebench==5.0.2` in a separate Python 3.13 venv
- Docker: 29.6.2, prebuilt instance images pulled from Docker Hub
- Gold control: the dataset's reference patch was resolved for every instance
  used to validate the harness.

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
`.swebench/runs/<run-id>/` (gitignored).  For a resolved run:

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
