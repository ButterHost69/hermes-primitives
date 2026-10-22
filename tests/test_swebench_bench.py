"""Tests for the SWE-bench benchmark adapter.

Everything here runs without a network or Docker: repositories are local git
repos, datasets are local files or monkeypatched pages, and the pipeline/agent
runners are replaced with stubs that make a predictable edit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import benchmarks.dataset as dataset_module
import benchmarks.repo as repo_module
import benchmarks.runner as runner_module
from benchmarks.dataset import (
    DatasetError,
    Instance,
    load_instances,
    select_instances,
)
from benchmarks.policy import repo_policy
from benchmarks.repo import (
    capture_patch,
    check_patch,
    prepare_repo,
    tarball_url,
)
from benchmarks.runner import (
    BenchmarkError,
    PredictionStore,
    RunOptions,
    TranscriptWriter,
    evaluate_predictions,
    model_slug,
    prediction_record,
    run_benchmark,
    run_instance,
)


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #
def make_instance(
    *,
    instance_id: str = "owner__repo-1",
    repo: str = "owner/repo",
    base_commit: str = "a" * 40,
    problem_statement: str = "Value should be 2.",
) -> Instance:
    return Instance(
        instance_id=instance_id,
        repo=repo,
        base_commit=base_commit,
        problem_statement=problem_statement,
        version="1.0",
        image="swebench/sweb.eval.x86_64.owner_1776_repo-1:latest",
        fail_to_pass=("pkg/tests/test_mod.py::test_value",),
        pass_to_pass=("pkg/tests/test_mod.py::test_import",),
    )


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, text=True, capture_output=True, check=True
    ).stdout


def make_repo(tmp_path: Path, *, content: str = "VALUE = 1\n") -> tuple[Path, str]:
    """Create a local git repo with one baseline commit and return its SHA."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "mod.py").write_text(content, encoding="utf-8")
    (repo / "README.md").write_text("# demo\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "core.fileMode", "false")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "baseline")
    return repo, _git(repo, "rev-parse", "HEAD").strip()


def make_tarball(tmp_path: Path, instance: Instance) -> Path:
    """Create a GitHub-shaped tarball for ``instance``."""
    source = tmp_path / "source"
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    (source / "README.md").write_text("# demo\n", encoding="utf-8")
    archive = tmp_path / "repo.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname=f"repo-{instance.base_commit[:7]}")
    return archive


def row(**overrides):
    base = {
        "instance_id": "owner__repo-1",
        "repo": "owner/repo",
        "base_commit": "a" * 40,
        "problem_statement": "Value should be 2.",
        "version": "1.0",
        "image": "swebench/sweb.eval.x86_64.owner_1776_repo-1:latest",
        "FAIL_TO_PASS": ["pkg/tests/test_mod.py::test_value"],
        "PASS_TO_PASS": '["pkg/tests/test_mod.py::test_import"]',
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------- #
# Dataset loading
# --------------------------------------------------------------------------- #
def test_instance_from_row_parses_fields_and_lists():
    instance = Instance.from_row(row())
    assert instance.instance_id == "owner__repo-1"
    assert instance.repo == "owner/repo"
    assert instance.fail_to_pass == ("pkg/tests/test_mod.py::test_value",)
    assert instance.pass_to_pass == ("pkg/tests/test_mod.py::test_import",)
    assert instance.image.startswith("swebench/")
    assert instance.raw["version"] == "1.0"


@pytest.mark.parametrize("field", ["instance_id", "repo", "base_commit", "problem_statement"])
def test_instance_from_row_rejects_missing_required_field(field):
    data = row()
    data[field] = ""
    with pytest.raises(DatasetError, match="missing"):
        Instance.from_row(data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("instance_id", "../escape"),
        ("repo", "owner/repo/extra"),
        ("base_commit", "not-a-sha"),
    ],
)
def test_instance_from_row_rejects_unsafe_values(field, value):
    data = row()
    data[field] = value
    with pytest.raises(DatasetError):
        Instance.from_row(data)


def test_load_instances_from_jsonl(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text(
        json.dumps(row(instance_id="a__b-1"))
        + "\n\n"
        + json.dumps(row(instance_id="a__b-2", base_commit="b" * 40)),
        encoding="utf-8",
    )
    instances = load_instances(path)
    assert [i.instance_id for i in instances] == ["a__b-1", "a__b-2"]


def test_load_instances_from_json_list(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps([row(instance_id="a__b-1")]), encoding="utf-8")
    assert [i.instance_id for i in load_instances(path)] == ["a__b-1"]


def test_load_instances_from_json_with_instances_key(tmp_path):
    path = tmp_path / "tasks.json"
    path.write_text(
        json.dumps({"instances": [row(instance_id="a__b-1")]}), encoding="utf-8"
    )
    assert [i.instance_id for i in load_instances(path)] == ["a__b-1"]


def test_load_instances_pages_through_the_dataset_viewer(tmp_path, monkeypatch):
    calls: list[int] = []

    def fake_page(dataset, split, offset):
        calls.append(offset)
        return {
            "rows": [{"row": row(instance_id=f"a__b-{offset}")}],
            "num_rows_total": 3,
        }

    monkeypatch.setattr(dataset_module, "_fetch_page", fake_page)
    monkeypatch.setattr(dataset_module, "PAGE_SIZE", 1)
    instances = load_instances(
        "owner/dataset", cache_dir=tmp_path / "cache", refresh=True
    )
    assert [i.instance_id for i in instances] == ["a__b-0", "a__b-1", "a__b-2"]
    assert calls == [0, 1, 2]


def test_load_instances_uses_the_cached_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dataset_module,
        "_fetch_page",
        lambda dataset, split, offset: {
            "rows": [{"row": row()}],
            "num_rows_total": 1,
        },
    )
    cache = tmp_path / "cache"
    assert len(load_instances("owner/dataset", cache_dir=cache, refresh=True)) == 1
    # A second load must not need the network at all.
    monkeypatch.setattr(
        dataset_module,
        "_fetch_page",
        lambda *args: (_ for _ in ()).throw(AssertionError("network used")),
    )
    assert len(load_instances("owner/dataset", cache_dir=cache)) == 1


def test_load_instances_rejects_missing_local_file(tmp_path):
    with pytest.raises(DatasetError, match="not found"):
        load_instances(tmp_path / "missing.json")


def test_load_instances_rejects_empty_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(
        dataset_module,
        "_fetch_page",
        lambda dataset, split, offset: {"rows": [], "num_rows_total": 0},
    )
    with pytest.raises(DatasetError, match="no task rows"):
        load_instances("owner/dataset", cache_dir=tmp_path / "cache")


def test_select_instances_filters_and_limits():
    instances = [make_instance(instance_id=f"owner__repo-{n}") for n in range(1, 5)]
    selected = select_instances(instances, only=["owner__repo-3", "owner__repo-1"])
    assert [i.instance_id for i in selected] == ["owner__repo-1", "owner__repo-3"]
    assert [i.instance_id for i in select_instances(selected, limit=1)] == [
        "owner__repo-1"
    ]


def test_select_instances_rejects_unknown_ids():
    with pytest.raises(DatasetError, match="unknown instance"):
        select_instances([make_instance()], only=["nope"])


def test_select_instances_rejects_bad_limit():
    with pytest.raises(ValueError, match="limit"):
        select_instances([make_instance()], limit=0)


# --------------------------------------------------------------------------- #
# Repository materialization and patches
# --------------------------------------------------------------------------- #
def test_tarball_url_is_the_base_commit_archive():
    instance = make_instance()
    assert tarball_url(instance) == (
        "https://codeload.github.com/owner/repo/tar.gz/" + "a" * 40
    )


def test_prepare_repo_materializes_and_resets(tmp_path, monkeypatch):
    instance = make_instance()
    archive = make_tarball(tmp_path, instance)

    def fake_download(url: str, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, target)

    monkeypatch.setattr(repo_module, "_download", fake_download)
    work = tmp_path / "work"
    checkout, baseline = prepare_repo(instance, work)
    assert (checkout / "pkg" / "mod.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert baseline == repo_module.baseline_sha(checkout)

    (checkout / "pkg" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    patch = capture_patch(checkout, baseline)
    assert "+VALUE = 2" in patch

    # Re-preparing the same instance restores the base commit.
    checkout, baseline = prepare_repo(instance, work)
    assert (checkout / "pkg" / "mod.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert capture_patch(checkout, baseline) == ""


def test_capture_patch_includes_new_files_and_excludes_artifacts(tmp_path):
    repo, baseline = make_repo(tmp_path)
    (repo / "pkg" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
    (repo / "pkg" / "new.py").write_text("NEW = True\n", encoding="utf-8")
    (repo / ".sessions").mkdir()
    (repo / ".sessions" / "session.jsonl").write_text("{}\n", encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "mod.pyc").write_bytes(b"\0")
    (repo / ".coverage").write_text("coverage\n", encoding="utf-8")

    patch = capture_patch(repo, baseline)
    assert "pkg/mod.py" in patch
    assert "pkg/new.py" in patch
    assert ".sessions" not in patch
    assert "__pycache__" not in patch
    assert ".coverage" not in patch
    assert check_patch(repo, baseline, patch) is None


def test_check_patch_rejects_a_patch_that_does_not_apply(tmp_path):
    repo, baseline = make_repo(tmp_path)
    bad = (
        "diff --git a/pkg/missing.py b/pkg/missing.py\n"
        "--- a/pkg/missing.py\n"
        "+++ b/pkg/missing.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    assert check_patch(repo, baseline, bad) is not None
    assert check_patch(repo, baseline, "") is None


# --------------------------------------------------------------------------- #
# Tool policy
# --------------------------------------------------------------------------- #
def test_repo_policy_allows_paths_inside_and_blocks_paths_outside(tmp_path):
    root = tmp_path / "repo"
    (root / "pkg").mkdir(parents=True)
    policy = repo_policy(root)

    def check(tool, value):
        return policy({"toolName": tool, "cwd": str(root), "input": {"path": value}})

    assert check("read", "pkg/mod.py")["action"] == "allow"
    assert check("write", "pkg/new.py")["action"] == "allow"
    assert check("read", "../secret")["action"] == "block"
    assert check("grep", "/home")["action"] == "block"
    assert check("find", "/")["action"] == "block"
    assert check("ls", str(tmp_path))["action"] == "block"
    assert policy({"toolName": "read", "cwd": str(root), "input": {}})["action"] == "allow"


def test_repo_policy_blocks_the_shell_and_unknown_tools(tmp_path):
    policy = repo_policy(tmp_path / "repo")
    assert policy({"toolName": "bash", "input": {"command": "ls"}})["action"] == "block"
    assert policy({"toolName": "codemode", "input": {}})["action"] == "block"
    assert policy({"toolName": "powershell", "input": {}})["action"] == "block"
    assert policy({"toolName": "mystery", "input": {}})["action"] == "block"
    assert policy({"toolName": "tool_search", "input": {}})["action"] == "allow"


def test_repo_policy_can_allow_the_shell_for_a_container():
    policy = repo_policy("/repo", allow_shell=True)
    assert policy({"toolName": "bash", "input": {"command": "ls"}})["action"] == "allow"
    # The path checks still apply when the shell is enabled.
    assert policy(
        {"toolName": "read", "cwd": "/repo", "input": {"path": "/etc/passwd"}}
    )["action"] == "block"


def test_run_pipeline_installs_the_repo_policy(tmp_path, monkeypatch):
    repo, _baseline = make_repo(tmp_path)
    created: list[FakePipeline] = []

    class RecordingPipeline(FakePipeline):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

    monkeypatch.setattr(runner_module, "Pipeline", RecordingPipeline)
    # Call the pipeline runner directly so the assertion is about the policy,
    # not about the checkout machinery.
    from benchmarks.runner import run_pipeline

    run_pipeline(make_instance(), repo, RunOptions(work_root=tmp_path), None)

    assert created[0].policy is not None
    assert created[0].policy(
        {"toolName": "read", "cwd": str(repo), "input": {"path": "/etc/passwd"}}
    )["action"] == "block"


# --------------------------------------------------------------------------- #
# Runners
# --------------------------------------------------------------------------- #
class FakePipeline:
    """A stand-in for Pipeline that edits the checkout it is given."""

    def __init__(self, codebase, model, thinking, api_key, observer=None, policy=None):
        self.codebase = Path(codebase)
        self.config = (model, thinking, api_key)
        self.observer = observer
        self.policy = policy
        self.steps: list[str] = []

    def run_step(self, name, previous, context=None):
        self.steps.append(name)
        if name == "reproduce":
            return {"summary": "reproduced", "reproduced": True}
        if name == "diagnose":
            return {
                "findings": [
                    {
                        "file": "pkg/mod.py",
                        "explanation": "VALUE is wrong",
                        "fixes": ["set it to 2"],
                    }
                ]
            }
        (self.codebase / "pkg" / "mod.py").write_text(
            "VALUE = 2\n", encoding="utf-8"
        )
        (self.codebase / "pkg" / "new.py").write_text("NEW = True\n", encoding="utf-8")
        return {"pkg/mod.py": '{"files_to_update": []}'}


def test_run_instance_pipeline_mode_records_a_patch(tmp_path, monkeypatch):
    repo, baseline = make_repo(tmp_path)
    instance = make_instance()
    monkeypatch.setattr(runner_module, "prepare_repo", lambda *args, **kwargs: (repo, baseline))
    monkeypatch.setattr(runner_module, "Pipeline", FakePipeline)
    options = RunOptions(mode="pipeline", keep_worktrees=True, work_root=tmp_path)

    record = run_instance(instance, options, tmp_path / "run")

    assert record["error"] is None
    assert record["has_patch"] is True
    assert record["patch_check_error"] is None
    assert "+VALUE = 2" in record["patch"]
    assert "pkg/new.py" in record["patch"]
    assert record["result"]["reproduce"]["reproduced"] is True
    assert record["result"]["diagnose"]["findings"][0]["file"] == "pkg/mod.py"
    instance_dir = tmp_path / "run" / "instances" / instance.instance_id
    assert (instance_dir / "patch.diff").is_file()
    assert (instance_dir / "result.json").is_file()
    assert (instance_dir / "transcript.jsonl").is_file()


def test_run_instance_pipeline_mode_can_skip_reproduce(tmp_path, monkeypatch):
    repo, baseline = make_repo(tmp_path)
    instance = make_instance()
    steps: list[str] = []

    class RecordingPipeline(FakePipeline):
        def run_step(self, name, previous, context=None):
            steps.append(name)
            return super().run_step(name, previous, context=context)

    monkeypatch.setattr(runner_module, "prepare_repo", lambda *args, **kwargs: (repo, baseline))
    monkeypatch.setattr(runner_module, "Pipeline", RecordingPipeline)
    options = RunOptions(
        mode="pipeline",
        keep_worktrees=True,
        work_root=tmp_path,
        skip_reproduce=True,
    )

    record = run_instance(instance, options, tmp_path / "run")

    assert steps == ["diagnose", "primitive_manager"]
    assert record["has_patch"] is True
    assert record["result"]["reproduce"]["reproduced"] is False


def test_run_instance_agent_mode_uses_a_single_session(tmp_path, monkeypatch):
    repo, baseline = make_repo(tmp_path)
    instance = make_instance()
    calls: list[dict] = []

    class FakeSession:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.repo = Path(kwargs["cwd"])

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def run(self, prompt):
            assert "Value should be 2." in prompt
            (self.repo / "pkg" / "mod.py").write_text("VALUE = 2\n", encoding="utf-8")
            return "done"

    monkeypatch.setattr(runner_module, "prepare_repo", lambda *args, **kwargs: (repo, baseline))
    monkeypatch.setattr(runner_module, "GatedPiSession", FakeSession)
    options = RunOptions(mode="agent", keep_worktrees=True, work_root=tmp_path)

    record = run_instance(instance, options, tmp_path / "run")

    assert record["error"] is None
    assert "+VALUE = 2" in record["patch"]
    assert calls[0]["persistent_session"] is False
    assert calls[0]["model"] == "opencode-go/cat-jam-free"
    assert calls[0]["thinking"] == "max"
    assert "policy" in calls[0]
    assert "bash" not in calls[0]["tools"]


def test_run_instance_records_an_error_without_raising(tmp_path, monkeypatch):
    instance = make_instance()

    def fail(*args, **kwargs):
        raise RuntimeError("no network")

    monkeypatch.setattr(runner_module, "prepare_repo", fail)
    options = RunOptions(work_root=tmp_path)

    record = run_instance(instance, options, tmp_path / "run")

    assert record["error"] == "RuntimeError: no network"
    assert record["patch"] == ""
    assert record["has_patch"] is False


def test_transcript_writer_serializes_unserializable_records(tmp_path):
    path = tmp_path / "transcript.jsonl"
    writer = TranscriptWriter(path)
    writer({"type": "message", "value": object()})
    writer.close()
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["type"] == "unserializable-record"


def test_prediction_store_round_trips(tmp_path):
    path = tmp_path / "predictions.jsonl"
    store = PredictionStore(path)
    store.put(
        {
            "instance_id": "a__b-1",
            "model_name_or_path": "hms",
            "model_patch": "diff",
        }
    )
    loaded = PredictionStore(path)
    loaded.load()
    assert loaded.records["a__b-1"]["model_patch"] == "diff"


def test_prediction_record_shape():
    record = prediction_record(make_instance(), "diff", model_name="hms")
    assert record == {
        "instance_id": "owner__repo-1",
        "model_name_or_path": "hms",
        "model_patch": "diff",
    }


def test_model_slug_is_harness_safe():
    options = RunOptions(model="opencode-go/cat-jam-free", thinking="max", mode="pipeline")
    slug = model_slug(options)
    assert slug == "hms-opencode-go-cat-jam-free-max-pipeline"
    assert "/" not in slug


def test_run_options_reject_unknown_mode_and_empty_values():
    with pytest.raises(BenchmarkError):
        RunOptions(mode="nope")
    with pytest.raises(BenchmarkError):
        RunOptions(model="")
    with pytest.raises(BenchmarkError):
        RunOptions(thinking="")


def test_run_benchmark_writes_predictions_and_resumes(tmp_path, monkeypatch):
    instances = [make_instance(instance_id="a__b-1"), make_instance(instance_id="a__b-2")]
    calls: list[str] = []

    def fake_run_instance(instance, options, run_dir):
        calls.append(instance.instance_id)
        return {
            "patch": f"diff for {instance.instance_id}",
            "has_patch": True,
            "patch_lines": 1,
            "patch_check_error": None,
            "error": None,
            "duration": 0.0,
        }

    monkeypatch.setattr(runner_module, "run_instance", fake_run_instance)
    options = RunOptions(mode="pipeline", work_root=tmp_path)
    run_dir = tmp_path / "run"
    summary = run_benchmark(instances, options, run_dir)

    assert summary["with_patch"] == 2
    assert calls == ["a__b-1", "a__b-2"]
    predictions = (run_dir / "predictions.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(predictions) == 2
    assert json.loads(predictions[0])["model_name_or_path"].startswith("hms-")

    calls.clear()
    summary = run_benchmark(instances, options, run_dir, resume=True)
    assert summary["skipped"] == 2
    assert calls == []


def test_run_benchmark_continues_after_a_failed_task(tmp_path, monkeypatch):
    instances = [make_instance(instance_id="a__b-1"), make_instance(instance_id="a__b-2")]

    def fake_run_instance(instance, options, run_dir):
        if instance.instance_id == "a__b-1":
            return {
                "patch": "",
                "has_patch": False,
                "patch_lines": 0,
                "patch_check_error": None,
                "error": "boom",
                "duration": 0.0,
            }
        return {
            "patch": "diff",
            "has_patch": True,
            "patch_lines": 1,
            "patch_check_error": None,
            "error": None,
            "duration": 0.0,
        }

    monkeypatch.setattr(runner_module, "run_instance", fake_run_instance)
    summary = run_benchmark(
        instances, RunOptions(work_root=tmp_path), tmp_path / "run"
    )
    assert summary["errors"] == 1
    assert summary["with_patch"] == 1


def test_evaluate_predictions_invokes_the_harness(tmp_path, monkeypatch):
    predictions = tmp_path / "predictions.jsonl"
    predictions.write_text("", encoding="utf-8")
    seen: dict = {}

    def fake_run(command, cwd=None, check=False):
        seen["command"] = command
        seen["cwd"] = cwd
        seen["check"] = check
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(runner_module.subprocess, "run", fake_run)
    code = evaluate_predictions(
        run_dir=tmp_path,
        predictions=predictions,
        dataset="SWE-bench/SWE-bench_Lite",
        instance_ids=["a__b-1", "a__b-2"],
        run_id="eval-run",
        swebench_bin="swebench",
    )
    assert code == 0
    command = seen["command"]
    assert command[:3] == ["swebench", "eval", "SWE-bench/SWE-bench_Lite"]
    assert command.count("--instance") == 2
    assert "--predictions" in command
    assert seen["cwd"] == str(tmp_path.resolve())


def test_evaluate_predictions_reports_a_missing_harness(tmp_path, monkeypatch, capsys):
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(runner_module.subprocess, "run", missing)
    code = evaluate_predictions(
        run_dir=tmp_path,
        predictions=tmp_path / "predictions.jsonl",
        dataset="SWE-bench/SWE-bench_Lite",
        instance_ids=["a__b-1"],
        run_id="eval-run",
    )
    assert code == 2
    assert "harness was not found" in capsys.readouterr().out
