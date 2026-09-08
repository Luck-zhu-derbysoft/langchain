"""Apply safe text cleanup and restage files before commit checks."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_TRAILING_SPACE = re.compile(rb"[ \t]+(?=\r?\n|$)")


def fix_file(filename: str) -> bool:
    """Remove trailing whitespace and ensure a final newline in one file."""
    path = Path(filename)
    if not path.is_file():
        return False

    original = path.read_bytes()
    if b"\x00" in original:
        return False

    fixed = _TRAILING_SPACE.sub(b"", original)
    if fixed and not fixed.endswith(b"\n"):
        fixed += b"\n"

    if fixed == original:
        return False

    path.write_bytes(fixed)
    return True


def main(filenames: list[str]) -> int:
    """Fix submitted text files and restage any files that changed."""
    changed_files = [filename for filename in filenames if fix_file(filename)]
    if changed_files:
        subprocess.run(["git", "add", "--", *changed_files], check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
