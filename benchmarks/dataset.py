"""Loading SWE-bench task instances.

A SWE-bench task is a repository checkout, a base commit, and a GitHub issue.
The official dataset ships those fields per row; this module reads a local
JSON/JSONL copy or pages through Hugging Face's dataset viewer, validates the
fields a run depends on, and selects the instances a run asked for.

Only the standard library is used here: the benchmark runner is expected to
work in the project's own environment, while the official evaluation harness
lives in its own environment with ``datasets`` and ``docker`` installed.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Hugging Face's dataset-viewer endpoint serves rows without the ``datasets``
#: package.  A page holds at most 100 rows.
HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"
PAGE_SIZE = 100
HTTP_TIMEOUT = 60.0

#: The default task set: small enough to run a first experiment on, official
#: enough that its instances appear on the SWE-bench leaderboard.
DEFAULT_DATASET = "SWE-bench/SWE-bench_Lite"

_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
_INSTANCE = re.compile(r"^[A-Za-z0-9_.-]+$")

_REQUIRED_FIELDS = ("instance_id", "repo", "base_commit", "problem_statement")


class DatasetError(RuntimeError):
    """The task set could not be loaded or is missing a required field."""


def _as_list(value: Any) -> tuple[str, ...]:
    """Normalize a dataset column that may be a list or a JSON string."""
    if value is None:
        return ()
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _slug(name: str) -> str:
    """A safe file name for a dataset id."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", name).strip("_") or "dataset"


@dataclass(frozen=True)
class Instance:
    """One SWE-bench task.

    ``raw`` keeps the rest of the dataset row (including the reference patch
    and test spec) for callers that want it; it is excluded from equality so
    two views of the same task still compare equal.
    """

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    version: str = ""
    image: str = ""
    fail_to_pass: tuple[str, ...] = ()
    pass_to_pass: tuple[str, ...] = ()
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Instance:
        """Build an instance from a dataset row, validating the fields a run needs."""
        if not isinstance(row, dict):
            raise DatasetError(f"task row is not an object: {type(row).__name__}")
        missing = [name for name in _REQUIRED_FIELDS if not row.get(name)]
        if missing:
            raise DatasetError(
                f"task row {row.get('instance_id', '<unknown>')} is missing: "
                + ", ".join(missing)
            )
        instance_id = str(row["instance_id"])
        repo = str(row["repo"])
        base_commit = str(row["base_commit"])
        if not _INSTANCE.match(instance_id):
            raise DatasetError(f"unsafe instance id: {instance_id!r}")
        if not _REPO.match(repo):
            raise DatasetError(f"invalid repository name: {repo!r}")
        if not _COMMIT.match(base_commit):
            raise DatasetError(f"invalid base commit for {instance_id}: {base_commit!r}")
        return cls(
            instance_id=instance_id,
            repo=repo,
            base_commit=base_commit,
            problem_statement=str(row["problem_statement"]),
            version=str(row.get("version") or ""),
            image=str(row.get("image") or ""),
            fail_to_pass=_as_list(row.get("FAIL_TO_PASS")),
            pass_to_pass=_as_list(row.get("PASS_TO_PASS")),
            raw=dict(row),
        )


def _rows_from_file(path: Path) -> list[dict[str, Any]]:
    """Read a local dataset: a JSON list or a JSONL file."""
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl":
        rows: list[dict[str, Any]] = []
        for number, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetError(f"{path}:{number}: invalid JSON: {exc}") from exc
            rows.append(row)
        return rows
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{path}: invalid JSON: {exc}") from exc
    if isinstance(loaded, dict) and isinstance(loaded.get("instances"), list):
        loaded = loaded["instances"]
    if not isinstance(loaded, list):
        raise DatasetError(f"{path}: expected a JSON list of task rows")
    return loaded


def _fetch_page(dataset: str, split: str, offset: int) -> dict[str, Any]:
    """Fetch one page of rows from Hugging Face's dataset viewer."""
    query = urllib.parse.urlencode(
        {
            "dataset": dataset,
            "config": "default",
            "split": split,
            "offset": offset,
            "length": PAGE_SIZE,
        }
    )
    request = urllib.request.Request(
        f"{HF_ROWS_URL}?{query}",
        headers={"User-Agent": "hms-swebench/0.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            return json.load(response)
    except (OSError, ValueError) as exc:
        raise DatasetError(
            f"could not fetch {dataset!r} ({split}) at offset {offset}: {exc}"
        ) from exc


def _rows_from_hf(
    dataset: str, split: str, *, cache_dir: Path | None, refresh: bool
) -> list[dict[str, Any]]:
    """Page through the dataset viewer, caching the merged rows on disk."""
    cache = None
    if cache_dir is not None:
        cache = cache_dir / f"{_slug(dataset)}.{split}.json"
        if cache.is_file() and not refresh:
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                cached = None
            if isinstance(cached, list):
                return cached

    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    while True:
        page = _fetch_page(dataset, split, offset)
        batch = page.get("rows") or []
        rows.extend(row.get("row") or {} for row in batch if isinstance(row, dict))
        if total is None:
            total = page.get("num_rows_total")
        offset += len(batch)
        if not batch or (isinstance(total, int) and offset >= total):
            break
        if len(batch) < PAGE_SIZE:
            break

    if cache is not None:
        cache.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache.with_suffix(".tmp")
        temporary.write_text(json.dumps(rows, indent=1), encoding="utf-8")
        temporary.replace(cache)
    return rows


def load_instances(
    source: str | Path = DEFAULT_DATASET,
    *,
    split: str = "test",
    cache_dir: str | Path | None = None,
    refresh: bool = False,
) -> list[Instance]:
    """Load every task in ``source``.

    ``source`` is a local ``.json``/``.jsonl`` file or a Hugging Face dataset
    id.  The cache only avoids a second network round-trip; ``refresh``
    re-reads the source.
    """
    path = Path(source)
    if path.is_file():
        rows = _rows_from_file(path)
    elif path.suffix in (".json", ".jsonl"):
        raise DatasetError(f"dataset file not found: {path}")
    else:
        rows = _rows_from_hf(
            str(source),
            split,
            cache_dir=Path(cache_dir) if cache_dir is not None else None,
            refresh=refresh,
        )
    if not rows:
        raise DatasetError(f"no task rows found in {source}")
    return [Instance.from_row(row) for row in rows]


def select_instances(
    instances: Sequence[Instance],
    *,
    only: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[Instance]:
    """Filter and truncate a task list, preserving the dataset order."""
    if only:
        wanted = set(only)
        known = {instance.instance_id for instance in instances}
        missing = sorted(wanted - known)
        if missing:
            raise DatasetError("unknown instance id(s): " + ", ".join(missing))
        instances = [i for i in instances if i.instance_id in wanted]
    if limit is not None:
        if limit < 1:
            raise ValueError(f"limit must be at least 1: {limit}")
        instances = list(instances)[:limit]
    return list(instances)


__all__ = [
    "DEFAULT_DATASET",
    "DatasetError",
    "Instance",
    "load_instances",
    "select_instances",
]
