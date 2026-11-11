"""Check what one run of the action in scan mode reported.

    expect_scan.py RESULT CODE [--outcome OUTCOME] [--finding ID FILE]

The run's outputs come in the environment: RESULT, CODE, JSON and SARIF, and OUTCOME for
the step's own outcome. FILE is the file of a use, as the repository names it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def read(name: str, problems: list[str]) -> dict:
    try:
        data = json.loads(Path(os.environ.get(name, "")).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"the {name.lower()} output cannot be read: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result")
    parser.add_argument("code")
    parser.add_argument("--outcome")
    parser.add_argument("--finding", nargs=2, metavar=("ID", "FILE"))
    wanted = parser.parse_args()

    problems: list[str] = []
    for name, value in (
        ("RESULT", wanted.result),
        ("CODE", wanted.code),
        ("OUTCOME", wanted.outcome),
    ):
        got = os.environ.get(name, "")
        if value is not None and got != value:
            problems.append(f"{name} is {got!r}, expected {value!r}")

    report = read("JSON", problems)
    found = [finding["id"] for finding in report.get("affected", [])]
    log = read("SARIF", problems)
    results = [
        result for run in log.get("runs", []) for result in run.get("results", [])
    ]
    files = [
        location["physicalLocation"]["artifactLocation"]["uri"]
        for result in results
        for location in result.get("locations", [])
    ]
    if wanted.finding:
        notice_id, file = wanted.finding
        if notice_id not in found:
            problems.append(f"no finding {notice_id} among {found}")
        if file not in files:
            problems.append(f"no SARIF result in {file} among {files}")
    elif found or results:
        problems.append(f"unexpected findings: {found}, in {files}")

    print(f"{len(found)} findings, {len(results)} SARIF results")
    if problems:
        sys.exit("\n".join(problems))
    print("As expected.")


if __name__ == "__main__":
    main()
