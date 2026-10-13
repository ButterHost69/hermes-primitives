class Num:
    def __init__(self, value):
        self.value = value


class BinOp:
    def __init__(self, op, left, right):
        self.op = op
        self.left = left
        self.right = right


class Parser:
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos]

    def consume(self):
        tok = self.tokens[self.pos]
        self.pos += 1
        return tok

    def parse_expression(self):
        node = self.parse_term()
        while self.peek()[0] in ("+", "-"):
            op = self.consume()[0]
            right = self.parse_term()
            node = BinOp(op, node, right)
        return node

    def parse_term(self):
        node = self.parse_factor()
        while self.peek()[0] in ("+", "-", "*", "/"):
            op = self.consume()[0]
            right = self.parse_factor()
            node = BinOp(op, node, right)
        return node

    def parse_factor(self):
        tok = self.peek()
        if tok[0] == "NUMBER":
            self.consume()
            return Num(tok[1])
        if tok[0] == "-":
            self.consume()
            return BinOp("-", Num(0), self.parse_factor())
        if tok[0] == "(":
            self.consume()
            node = self.parse_expression()
            if self.peek()[0] != ")":
                raise ValueError("expected ')'")
            self.consume()
            return node
        raise ValueError(f"unexpected token {tok}")
