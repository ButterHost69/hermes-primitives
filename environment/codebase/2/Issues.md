# Issue — Operator precedence is not respected

## Summary

Arithmetic expressions are evaluated strictly left-to-right, so `*` and `/` are
given the same precedence as `+` and `-`. This produces wrong results for any
expression that mixes operators.

## Reproduce

```bash
python3 -m unittest discover -s tests -t .
```

The tests `test_precedence_multiplication_over_addition` and
`test_precedence_division_over_subtraction` fail.

## Expected behaviour

`2 + 3 * 4` should evaluate to `14` (multiplication binds tighter than
addition), and `10 - 4 / 2` should evaluate to `8`.

## Actual behaviour

`2 + 3 * 4` evaluates to `20` and `10 - 4 / 2` evaluates to `3.0`.

## Where to look

The expression grammar lives in `calclang/parser.py`.
