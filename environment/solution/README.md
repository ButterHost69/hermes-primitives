# Solution oracle tests

This folder holds the reference diffs **and** an independent oracle test suite
for each buggy project under `../codebase/<n>/`.

The oracle tests import the *real* code from `../codebase/<n>/` (they do not
copy it and they do not rely on the tests shipped with the project). Each suite:

- **fails today**, because the injected bug is still present, and
- **passes once the corresponding fix is applied** to `../codebase/<n>/`.

| # | Suite | Codebase under test | Bug it detects |
|---|-------|---------------------|----------------|
| 1 | `1/test_discount_fix.js` | `codebase/1/server` (ShopFront) | discount applied as a raw number instead of a percent |
| 2 | `2/test_precedence_fix.py` | `codebase/2` (CalcLang) | operator precedence lost (`2 + 3 * 4` → 20) |
| 3 | `3/test_ipv4_endianness_fix.py` | `codebase/3` (PacketWatch) | IPv4 `total_length` parsed little-endian |
| 4 | `4/test_backoff_fix.py` | `codebase/4` (QueueMaster) | exponential backoff one step too aggressive |
| 5 | `5/test_alg_none_fix.py` | `codebase/5` (AuthCore) | `alg: none` tokens accepted as valid |

## Run everything

```bash
bash solution/run_all.sh
```

## Run one suite

```bash
node solution/1/test_discount_fix.js          # or: node --test solution/1/test_discount_fix.js
python3 solution/2/test_precedence_fix.py
python3 solution/3/test_ipv4_endianness_fix.py
python3 solution/4/test_backoff_fix.py
python3 solution/5/test_alg_none_fix.py
```

The Python suites also work under pytest:

```bash
python3 -m pytest solution/2 solution/3 solution/4 solution/5
```

## Verifying a fix

Apply a reference diff to the matching codebase, then rerun the suite:

```bash
cd codebase/2 && git apply ../solution/2/calclang/parser.py.diff
python3 ../solution/2/test_precedence_fix.py
```

Each Python suite locates `codebase/<n>` by walking up from its own location and
from the current working directory, so it can be run from the repository root,
from this folder, or from inside the codebase.
