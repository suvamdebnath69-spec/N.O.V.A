"""js_check.py — lightweight JS syntax sanity for CI-less machines.

Checks bracket balance and unterminated strings/comments via a small
state machine (covers ', ", ` strings with backslash escapes, line and
block comments). Not a full parser, but catches the classic breakages
from hand-edited scripts.

Usage: python tests/js_check.py Ui/script.js Ui/beast2.js
"""

import sys


def check(path: str) -> bool:
    src = open(path, encoding="utf-8").read()
    stack = []
    pairs = {"(": ")", "[": "]", "{": "}"}
    mode = None  # None | 'line' | 'block' | quote char
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if mode is None:
            if c == "/" and nxt == "/":
                mode = "line"
                i += 2
                continue
            if c == "/" and nxt == "*":
                mode = "block"
                i += 2
                continue
            if c in "'\"`":
                mode = c
                i += 1
                continue
            if c in "([{":
                stack.append(c)
            elif c in ")]}":
                if not stack or pairs[stack[-1]] != c:
                    print(f"{path}: MISMATCH '{c}' at offset {i}")
                    return False
                stack.pop()
        elif mode == "line":
            if c == "\n":
                mode = None
        elif mode == "block":
            if c == "*" and nxt == "/":
                mode = None
                i += 2
                continue
        else:  # inside a string
            if c == "\\":
                i += 2
                continue
            if c == mode:
                mode = None
        i += 1
    if stack or mode is not None:
        print(f"{path}: UNBALANCED (stack={stack}, mode={mode})")
        return False
    print(f"{path}: OK")
    return True


if __name__ == "__main__":
    ok = all(check(p) for p in sys.argv[1:])
    sys.exit(0 if ok else 1)
