# Codebase 5 — Longest substring without repeating characters

**Difficulty: 5/5 (hardest)**

`length_of_longest_substring` returns the length of the longest substring of `s`
that contains no repeated characters.

## Expected behaviour

- `length_of_longest_substring("abcdef")` == `6`
- `length_of_longest_substring("dvdf")` == `3`
- `length_of_longest_substring("abcade")` == `5`
- `length_of_longest_substring("abcabcbb")` == `3`
- `length_of_longest_substring("bbbbb")` == `1`
- `length_of_longest_substring("")` == `0`

## Run the tests

```
python -m pytest
```

There is exactly one bug. Fix it so the tests pass.
