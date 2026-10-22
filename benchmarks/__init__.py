"""SWE-bench benchmark adapter for the hms pipeline.

The adapter is intentionally separate from ``pipeline`` and ``roles``: it
materializes a benchmark task, invokes the existing pipeline, and turns the
resulting tree into the patch format the official SWE-bench harness consumes.
"""

from benchmarks.dataset import DatasetError, Instance, load_instances, select_instances
from benchmarks.policy import repo_policy
from benchmarks.repo import RepoError, capture_patch, check_patch, prepare_repo
from benchmarks.runner import (
    BenchmarkError,
    RunOptions,
    evaluate_predictions,
    run_benchmark,
    run_instance,
)

__all__ = [
    "BenchmarkError",
    "DatasetError",
    "Instance",
    "RepoError",
    "RunOptions",
    "capture_patch",
    "check_patch",
    "evaluate_predictions",
    "load_instances",
    "prepare_repo",
    "repo_policy",
    "run_benchmark",
    "run_instance",
    "select_instances",
]
