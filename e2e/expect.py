"""Check what one run of the action reported.

    expect.py RESULT CODE [--outcome OUTCOME] [--change KIND PATH]

The run's outputs come in the environment: RESULT, CODE and JSON, and OUTCOME for the
step's own outcome.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result")
    parser.add_argument("code")
    parser.add_argument("--outcome")
    parser.add_argument("--change", nargs=2, metavar=("KIND", "PATH"))
    wanted = parser.parse_args()

    problems = []
    for name, value in (
        ("RESULT", wanted.result),
        ("CODE", wanted.code),
        ("OUTCOME", wanted.outcome),
    ):
        got = os.environ.get(name, "")
        if value is not None and got != value:
            problems.append(f"{name} is {got!r}, expected {value!r}")

    try:
        results = json.loads(
            Path(os.environ.get("JSON", "")).read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        results = []
        problems.append(f"the json output cannot be read: {exc}")
    found = [
        (change["kind"], change["path"]) for r in results for change in r["changes"]
    ]
    if wanted.change and tuple(wanted.change) not in found:
        problems.append(f"no {wanted.change[0]} of {wanted.change[1]} among {found}")
    if not wanted.change and found:
        problems.append(f"unexpected changes: {found}")

    for r in results:
        print(f"{r['endpoint_id']}: {r['outcome']}, {len(r['changes'])} changes")
    if problems:
        sys.exit("\n".join(problems))
    print("As expected.")


if __name__ == "__main__":
    main()
