# Codebase 4 — Visitor registry

**Difficulty: 4/5 (hard)**

`record_visit` records a visitor id and returns the ids recorded during that call.
Every call should produce a fresh list that contains only the visitor recorded in
that call.

## Expected behaviour

- `record_visit(1)` == `[1]`
- `record_visit(2)` == `[2]`
- `record_visit(3)` == `[3]`

(Each call is independent — the list must not accumulate across calls.)

## Run the tests

```
python -m pytest
```

There is exactly one bug. Fix it so the tests pass.
