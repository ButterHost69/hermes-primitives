# Bug-fixing exercise environments

Five small, self-contained codebases. Each one contains **exactly one bug**,
with difficulty increasing from 1 (trivial) to 5 (hard).

## Structure

```
environment/
├── codebase/          # the buggy codebases
│   ├── 1/  ...  5/
│   │   ├── <module>.py      # source (contains the bug)
│   │   ├── test_<module>.py # tests that encode the expected behaviour
│   │   └── README.md
└── solution/          # reference solutions (diffs only)
    ├── 1/  ...  5/
    │   └── <module>.py.diff
```

## Codebases

| # | Topic | Bug class | Difficulty |
|---|-------|-----------|------------|
| 1 | Temperature converter | Wrong magic constant | 1/5 |
| 2 | Palindrome check | Off-by-one loop bound | 2/5 |
| 3 | Merge sort | Missing leftover handling | 3/5 |
| 4 | Visitor registry | Mutable default argument | 4/5 |
| 5 | Longest substring (sliding window) | Subtle window-start logic | 5/5 |

## How to use

1. Enter a codebase: `cd codebase/<n>`
2. Run the tests: `python -m pytest` (they fail — that is the bug)
3. Fix the single bug in `<module>.py`
4. Confirm the tests pass

Each `solution/<n>/<module>.py.diff` is a unified git diff that, when applied to
the buggy codebase, fixes the bug:

```
cd codebase/<n> && git apply ../solution/<n>/<module>.py.diff
```

(or `patch -p1 < ../solution/<n>/<module>.py.diff`)
