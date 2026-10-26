"""Helpers for the Tiltmeter GitHub Action. Runs inside Tiltmeter's own environment."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ICONS = {"breaking": "🔴", "warning": "🟠", "info": "🔵"}
OUTCOMES = {
    "drift": "drift",
    "changed": "info changes",
    "clean": "clean",
    "baseline": "baseline recorded",
    "error": "check failed",
}
VERDICTS = {
    0: "No drift.",
    1: "Warning-level drift.",
    2: "Breaking drift.",
    3: "A check could not run.",
    4: "Configuration or usage error. See the step log.",
}
NOTICE_VERDICTS = {
    0: "Every new notice was handled.",
    1: "An agent left questions for a human.",
    3: "A fix or a feed failed. See the step log.",
    4: "Configuration or usage error. See the step log.",
}
NOTICE_RESULTS = {
    "unaffected": "not affected",
    "opened": "pull request opened",
    "pushed": "branch pushed",
    "committed": "committed",
    "unchanged": "nothing to change",
    "exists": "already has a branch",
    "blocked": "touched a protected file",
    "failed": "failed",
    "discarded": "discarded",
}


def state_dir(config: str) -> None:
    """Print the baseline folder as a GITHUB_OUTPUT line, mirroring Tiltmeter's own rule."""
    try:
        data = yaml.safe_load(Path(config).read_text(encoding="utf-8")) or {}
        state = str(data.get("state_dir") or ".tiltmeter")
    except (OSError, yaml.YAMLError, AttributeError):
        state = ".tiltmeter"
    state = os.path.expanduser(state)
    if not os.path.isabs(state):
        state = os.path.normpath(os.path.join(os.path.dirname(config), state))
    print(f"dir={state}")


def _cell(value: Any) -> str:
    return (
        str(value).replace("|", "\\|").replace("\n", " ") if value is not None else ""
    )


def summary(results_path: str, code: str, summary_path: str) -> None:
    """Print the results to the log and, when asked, write them to the job summary."""
    try:
        results: list[dict[str, Any]] = json.loads(
            Path(results_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        results = []

    verdict = VERDICTS.get(int(code), f"Exit code {code}.")
    md = ["## Tiltmeter", "", f"**{verdict}** Exit code `{code}`.", ""]
    log = [f"Tiltmeter: {verdict} (exit code {code})"]

    if results:
        md += ["| Endpoint | Status | Result | Changes |", "|---|---|---|---|"]
        for r in results:
            severity = r.get("max_severity")
            outcome = OUTCOMES.get(r.get("outcome", ""), r.get("outcome", ""))
            label = (
                f"{ICONS.get(severity, '')} {severity}".strip() if severity else outcome
            )
            status = r.get("status_code") or "—"
            md.append(
                f"| `{_cell(r['endpoint_id'])}` | {status} | {_cell(label)} | {len(r['changes'])} |"
            )
            log.append(
                f"  {r['endpoint_id']}: {severity or outcome}, {len(r['changes'])} changes"
            )

        for r in results:
            if not r["changes"] and not r.get("error"):
                continue
            md += ["", f"### `{_cell(r['endpoint_id'])}`", ""]
            if r.get("error"):
                md.append(f"> {_cell(r['error'])}")
                log.append(f"  {r['endpoint_id']}: {r['error']}")
            if r["changes"]:
                md += [
                    "| Severity | Change | Path | Before | After | Note |",
                    "|---|---|---|---|---|---|",
                ]
                for c in r["changes"]:
                    md.append(
                        f"| {ICONS.get(c['severity'], '')} {c['severity']} | {c['kind']} "
                        f"| `{_cell(c['path'])}` | {_cell(c.get('before'))} | {_cell(c.get('after'))} "
                        f"| {_cell(c.get('hint'))} |"
                    )
                    log.append(f"    {c['severity']:<8} {c['message']}")
            explanation = r.get("explanation")
            if explanation:
                md += [
                    "",
                    f"**What changed.** {explanation['summary']}",
                    "",
                    f"**Impact.** {explanation['impact']}",
                    "",
                    f"**Next step.** {explanation['suggested_action']}",
                ]

    print("\n".join(log))
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")


_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]+")


def _line(value: Any) -> str:
    """Third-party text on one log line, so it cannot start a workflow command."""
    return " ".join(_UNPRINTABLE.sub(" ", str(value or "")).split())


def _code(value: Any) -> str:
    """Text that may come from a provider's notice, as inert inline code."""
    return f"`{_cell(value).replace('`', "'")}`" if value else ""


def notices_summary(results_path: str, code: str, summary_path: str) -> None:
    """Print what `tiltmeter notices apply` did and, when asked, write it to the job summary."""
    try:
        data: dict[str, Any] = json.loads(
            Path(results_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        data = {}
    verdict = NOTICE_VERDICTS.get(int(code), f"Exit code {code}.")
    md = ["## Tiltmeter notices", "", f"**{verdict}** Exit code `{code}`.", ""]
    log = [f"Tiltmeter notices: {verdict} (exit code {code})"]

    for feed in data.get("feeds", []):
        if feed.get("error"):
            md.append(f"> {_code(feed['subscription'])}: {_code(feed['error'])}")
            log.append(f"  {_line(feed['subscription'])}: {_line(feed['error'])}")
        for entry in feed.get("entries", []):
            if entry.get("state") == "rejected":
                md.append(
                    f"> {_code(feed['subscription'])} rejected {_code(entry.get('id'))}: "
                    f"{_code(entry.get('problem'))}"
                )
                log.append(
                    f"  rejected {_line(entry.get('id'))}: {_line(entry.get('problem'))}"
                )

    applied = data.get("applied", [])
    if applied:
        md += ["", "| Provider | Notice | Result | Details |", "|---|---|---|---|"]
        for item in applied:
            result = NOTICE_RESULTS.get(item.get("status", ""), item.get("status", ""))
            detail = item.get("detail") or ""
            outcome = item.get("outcome") or {}
            if detail and detail == outcome.get("pr_url"):
                detail = f"[{_cell(detail)}]({detail})"  # the URL gh returned, not the notice's
            else:
                detail = _code(detail)
            md.append(
                f"| {_code(item.get('provider'))} | {_code(item.get('notice_id'))} "
                f"| {result} | {detail} |"
            )
            log.append(
                f"  {_line(item.get('provider'))} {_line(item.get('notice_id'))}: "
                f"{result} {_line(item.get('detail'))}"
            )
    elif not data.get("feeds"):
        md.append("No results.")
    else:
        md.append("No new notices.")

    print("\n".join(log))
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")


MARKER = "<!-- tiltmeter-notice-draft -->"
DOCS = "https://github.com/tiltmeter-dev/tiltmeter/blob/main/docs/notices.md"
FENCE = "`" * 4


def _fenced(text: str) -> list[str]:
    """Text in a code block that nothing inside can close."""
    return [FENCE + "json", text.replace(FENCE, "'" * 4), FENCE]


def draft_comment(draft_path: str, spec: str, notices_dir: str, out_path: str) -> None:
    """Write the pull request comment for a draft notice, or for no change at all."""
    try:
        text = Path(draft_path).read_text(encoding="utf-8") if draft_path else ""
        draft = json.loads(text) if text.strip() else None
    except (OSError, ValueError):
        draft = None
    if not isinstance(draft, dict):
        body = [
            MARKER,
            "### API change notice",
            "",
            f"The latest version of this pull request changes no responses in {_code(spec)}.",
        ]
        print("No response changes: no notice needed.")
    else:
        name = f"{notices_dir.rstrip('/')}/{draft.get('id', 'notice')}.json"
        body = [
            MARKER,
            "### API change notice",
            "",
            (
                f"This pull request changes the responses described by {_code(spec)}. "
                "Customers who follow your notices can get a pull request that adapts their "
                f"code: save this draft as {_code(name)}, review the summary, add a `note` "
                "wherever a new format matters, and set `effective_at` to when the old "
                "responses stop."
            ),
            "",
            "| Endpoint | Change | Path | Before | After |",
            "|---|---|---|---|---|",
        ]
        for endpoint in draft.get("endpoints", []):
            where = _code(f"{endpoint.get('method')} {endpoint.get('url')}")
            for change in endpoint.get("changes", []):
                after = _code(change.get("after")) or "—"
                if change.get("renamed_to"):
                    after = f"renamed to {_code(change['renamed_to'])}"
                body.append(
                    f"| {where} | {_code(change.get('kind'))} | {_code(change.get('path'))} "
                    f"| {_code(change.get('before')) or '—'} | {after} |"
                )
        body += [
            "",
            "<details><summary>Draft notice</summary>",
            "",
            *_fenced(json.dumps(draft, indent=2, ensure_ascii=False)),
            "",
            "</details>",
            "",
            (
                f"Merging publishes the notices in {_code(notices_dir)} to your feed. "
                f"[How it works]({DOCS})"
            ),
        ]
        print(f"Drafted notice {draft.get('id')}: see the pull request comment.")
    Path(out_path).write_text("\n".join(body) + "\n", encoding="utf-8")


def _gh(*args: str) -> str:
    completed = subprocess.run(
        ["gh", *args], capture_output=True, text=True, encoding="utf-8", check=False
    )
    if completed.returncode != 0:
        raise SystemExit(f"gh {args[0]} failed: {completed.stderr.strip()}")
    return completed.stdout


def _login() -> str:
    """Who the token comments as: its user, or the Actions bot for GITHUB_TOKEN."""
    completed = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    login = completed.stdout.strip() if completed.returncode == 0 else ""
    return (
        login
        if re.fullmatch(r"[A-Za-z0-9-]+(\[bot\])?", login)
        else "github-actions[bot]"
    )


def upsert_comment(body_path: str) -> None:
    """Keep one draft comment per pull request; a "no change" one only edits an existing one."""
    with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as handle:
        number = json.load(handle)["pull_request"]["number"]
    repo = os.environ["GITHUB_REPOSITORY"]
    me = _login()
    # Only our own comment: anyone can post one that starts with the marker.
    found = _gh(
        "api",
        "--paginate",
        f"repos/{repo}/issues/{number}/comments",
        "--jq",
        f'.[] | select(.user.login == "{me}" and (.body | startswith("{MARKER}"))) | .id',
    ).split()
    changed = "save this draft as" in Path(body_path).read_text(encoding="utf-8")
    body = f"body=@{body_path}"
    if found:
        _gh(
            "api", "-X", "PATCH", f"repos/{repo}/issues/comments/{found[0]}", "-F", body
        )
    elif changed:
        _gh("api", "-X", "POST", f"repos/{repo}/issues/{number}/comments", "-F", body)


def main() -> None:
    command, *args = sys.argv[1:]
    if command == "state-dir":
        state_dir(*args)
    elif command == "summary":
        summary(*args)
    elif command == "notices-summary":
        notices_summary(*args)
    elif command == "draft-comment":
        draft_comment(*args)
    elif command == "upsert-comment":
        upsert_comment(*args)
    else:
        raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
