# Codebase 3 — Merge sort

**Difficulty: 3/5 (medium)**

A merge sort implementation. `merge` combines two already-sorted lists; `merge_sort`
returns a sorted copy of a list.

## Expected behaviour

- `merge([1, 3, 5], [2, 4, 6])` == `[1, 2, 3, 4, 5, 6]`
- `merge([], [1, 2, 3])` == `[1, 2, 3]`
- `merge([1, 2, 3], [])` == `[1, 2, 3]`
- `merge_sort([5, 2, 9, 1, 5, 6])` == `[1, 2, 5, 5, 6, 9]`

## Run the tests

```
python -m pytest
```

There is exactly one bug. Fix it so the tests pass.
