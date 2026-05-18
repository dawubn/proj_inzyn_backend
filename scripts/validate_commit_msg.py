#!/usr/bin/env python3
"""
Validates commit message format: type:TICKET-ID:Description

Accepted format:
    feat:pi-163:Add pre-commit checker and update README
    fix:PP-12:Correct health check database name
    chore:pi-5:Setup docker-compose

Rules:
    - type must be one of the allowed types
    - ticket must match pattern: letters + dash + digits (e.g. pi-163, PP-12)
    - description must be non-empty
"""

import re
import sys
from pathlib import Path

ALLOWED_TYPES = ["feat", "fix", "docs", "refactor", "test", "chore", "ci", "perf"]

PATTERN = re.compile(
    r"^(?P<type>" + "|".join(ALLOWED_TYPES) + r")"
    r":(?P<ticket>[a-zA-Z]+-\d+)"
    r":(?P<description>.+)$"
)

MERGE_PATTERN = re.compile(r"^Merge ")
REVERT_PATTERN = re.compile(r"^Revert ")


def main() -> int:
    commit_msg_file = sys.argv[1]
    message = Path(commit_msg_file).read_text().splitlines()[0].strip()

    # Allow merge and revert commits
    if MERGE_PATTERN.match(message) or REVERT_PATTERN.match(message):
        return 0

    match = PATTERN.match(message)
    if not match:
        sys.stderr.write("\n[commit-msg] Nieprawidłowy format wiadomości commita.\n")
        sys.stderr.write(f"  Otrzymano : {message!r}\n")
        sys.stderr.write("  Wymagany  : type:TICKET-ID:Opis\n")
        sys.stderr.write("  Przykład  : feat:pi-163:Add pre-commit checker\n")
        sys.stderr.write(f"  Typy      : {', '.join(ALLOWED_TYPES)}\n\n")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
