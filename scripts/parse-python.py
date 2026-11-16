"""Parse Python source without importing modules or writing bytecode."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path


def main() -> int:
    files = json.load(sys.stdin)
    failures = []
    for name in files:
        try:
            ast.parse(Path(name).read_text(encoding="utf-8-sig"), filename=name)
        except (SyntaxError, UnicodeError) as error:
            failures.append(f"{name}: {error}")
    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"Python syntax: {len(files)} files, {len(failures)} failures")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
