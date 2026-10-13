# CalcLang

CalcLang is a small arithmetic expression compiler/interpreter written in pure
Python (standard library only, no external dependencies). It parses arithmetic
expressions such as `(2 + 3) * 4` into an abstract syntax tree (AST) and
evaluates the tree to a numeric result.

## Pipeline

CalcLang processes an expression in three stages:

1. **Lexer** (`calclang/lexer.py`) — Converts the raw input string into a list of
   tokens. Numbers are emitted as `("NUMBER", float)` and operators/parentheses
   as single-character tokens, terminated by an `("EOF", None)` token.

2. **Parser** (`calclang/parser.py`) — A recursive-descent parser that consumes
   the token stream and builds an AST. It enforces standard operator precedence
   (`*` and `/` bind tighter than `+` and `-`), supports parentheses, and handles
   unary minus. AST nodes are `Num` (a literal) and `BinOp` (a binary operation).

3. **Evaluator** (`calclang/evaluator.py`) — Walks the AST recursively and
   computes the result. Division by zero raises `ZeroDivisionError`.

The public entry point is `eval_expression`, exported from `calclang`:

```python
from calclang import eval_expression

eval_expression("2 + 3 * 4")  # -> 14
eval_expression("(2 + 3) * 4")  # -> 20
```

## Running the tests

From the project root, run:

```bash
python3 -m unittest discover -s tests -t . -v
```

or, if you have pytest available:

```bash
python3 -m pytest
```
