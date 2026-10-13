# Bug-fixing exercise environments

Five **real-world** codebases, each simulating an actual system. Each contains
**exactly one** injected bug (or a concrete feature request), with difficulty
increasing from 1 → 5.

Each project is built by a scaffold subagent, then a separate subagent injects
the bug. Every project ships with:

- `README.md` — what the project is, how to run it, and how to run its tests
- `Issues.md` — the issue to fix, described at a high level (no spoilers)
- tests that encode the expected behaviour (fail on the bug, pass on the fix)

## Structure

```
environment/
├── codebase/          # the buggy projects
│   ├── 1/ ... 5/
│   └── solution/ ...  # (below)
└── solution/          # reference solutions (diffs only)
    ├── 1/ ... 5/
    │   └── <file>.diff
```

## The five use cases

| # | Project | Stack / domain | Bug class | Difficulty |
|---|---------|----------------|-----------|------------|
| 1 | ShopFront — e-commerce store | MERN (MongoDB/Express/React/Node) | Discount applied as raw number, not a percent | 1/5 |
| 2 | CalcLang — arithmetic compiler | Python lexer/parser/evaluator | Operator precedence lost (`2 + 3 * 4` → 20) | 2/5 |
| 3 | PacketWatch — packet monitor | Python network parser (Ethernet/IPv4/TCP) | IPv4 field parsed little-endian | 3/5 |
| 4 | QueueMaster — task scheduler | Python priority queue + retry backoff | Off-by-one in exponential backoff | 4/5 |
| 5 | AuthCore — JWT auth service | Python HMAC-signed JWT | `alg: none` tokens accepted as valid | 5/5 |

## How to use

1. Enter a project: `cd codebase/<n>`
2. Run the tests — they fail (that is the bug). See each `README.md` for the command.
3. Fix the bug. See `Issues.md` for the issue description.
4. Confirm the tests pass.

Each `solution/<n>/<file>.diff` is a unified git diff that, applied to the buggy
project, fixes the bug:

```
cd codebase/<n> && git apply ../solution/<n>/<file>.diff
```
