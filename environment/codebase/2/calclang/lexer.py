import re

Token = tuple  # (type, value)

TOKEN_RE = re.compile(r"\s*(?:(\d+\.?\d*)|([+\-*/()]))")

def tokenize(text: str) -> list:
    tokens = []
    pos = 0
    while pos < len(text):
        m = TOKEN_RE.match(text, pos)
        if not m:
            raise ValueError(f"unexpected character at position {pos}: {text[pos]!r}")
        pos = m.end()
        if m.group(1) is not None:
            tokens.append(("NUMBER", float(m.group(1))))
        else:
            tokens.append((m.group(2), m.group(2)))
    tokens.append(("EOF", None))
    return tokens
