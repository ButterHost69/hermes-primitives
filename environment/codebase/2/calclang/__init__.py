from .lexer import tokenize
from .parser import Parser
from .evaluator import evaluate


def eval_expression(text: str) -> float:
    return evaluate(Parser(tokenize(text)).parse_expression())
