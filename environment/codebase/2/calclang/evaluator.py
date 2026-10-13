from .parser import BinOp, Num


def evaluate(node):
    if isinstance(node, Num):
        return node.value
    if isinstance(node, BinOp):
        left = evaluate(node.left)
        right = evaluate(node.right)
        if node.op == "+":
            return left + right
        if node.op == "-":
            return left - right
        if node.op == "*":
            return left * right
        if node.op == "/":
            if right == 0:
                raise ZeroDivisionError("division by zero")
            return left / right
    raise TypeError(f"unknown node {node!r}")
