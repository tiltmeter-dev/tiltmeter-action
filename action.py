"""Helpers for the Tiltmeter GitHub Action. Runs inside Tiltmeter's own environment."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
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
SCAN_VERDICTS = {
    0: "The code uses no known API change.",
    1: "The code uses a known API change.",
    3: "The catalog could not be read, or a fix failed. See the step log.",
    4: "Configuration or usage error. See the step log.",
}
FIX_RESULTS = {
    "opened": "pull request opened",
    "updated": "pull request updated",
    "pushed": "branch pushed",
    "committed": "committed",
    "unchanged": "nothing to change",
    "exists": "already has a branch",
    "blocked": "touched a protected file",
    "discarded": "discarded",
    "skipped": "skipped",
    "failed": "failed",
}
# GitHub shows at most ten annotations of each level per step.
MAX_ANNOTATIONS = 10
MAX_PLACES = 10
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


_UNPRINTABLE = re.compile(r"[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]+")


def _line(value: Any) -> str:
    """Third-party text on one log line, so it cannot start a workflow command."""
    return " ".join(_UNPRINTABLE.sub(" ", str(value or "")).split())


def _code(value: Any) -> str:
    """Text that may come from an API or a provider's notice, as inert inline code."""
    return f"`{_cell(value).replace('`', "'")}`" if value else ""


FENCE = "`" * 4


def _fenced(text: str, language: str = "json") -> list[str]:
    """Text in a code block that nothing inside can close."""
    return [FENCE + language, text.replace(FENCE, "'" * 4), FENCE]


NEW_STATE = (
    "Nothing was restored from the Actions cache, so this run started from scratch: "
    "{what}. That is expected on the first run. Later it means GitHub removed the cache, "
    "which it does with entries nobody used for 7 days: schedule the workflow more often "
    "than weekly."
)


def _new_state(state: str, what: str, md: list[str], log: list[str]) -> None:
    """Say so when the cache held nothing: a run that starts over finds nothing, quietly."""
    if state != "new":
        return
    message = NEW_STATE.format(what=what)
    md += [f"> {message}", ""]
    log.append(f"::notice title=Tiltmeter::{message}")


def summary(results_path: str, code: str, summary_path: str, state: str = "") -> None:
    """Print the results to the log and, when asked, write them to the job summary.

    Paths, hints, errors and explanations come from the API being checked, or from a
    model that read it: they go into the summary as code, and into the log on one line.
    """
    try:
        results: list[dict[str, Any]] = json.loads(
            Path(results_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        results = []

    verdict = VERDICTS.get(int(code), f"Exit code {code}.")
    md = ["## Tiltmeter", "", f"**{verdict}** Exit code `{code}`.", ""]
    log = [f"Tiltmeter: {verdict} (exit code {code})"]
    _new_state(state, "it recorded a new baseline and could not report drift", md, log)

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
                f"| {_code(r['endpoint_id'])} | {status} | {_cell(label)} | {len(r['changes'])} |"
            )
            log.append(
                f"  {_line(r['endpoint_id'])}: {severity or outcome}, {len(r['changes'])} changes"
            )

        for r in results:
            notes = r.get("notes") or []
            if not r["changes"] and not r.get("error") and not notes:
                continue
            md += ["", f"### {_code(r['endpoint_id'])}", ""]
            if r.get("error"):
                md.append(f"> {_code(r['error'])}")
                log.append(f"  {_line(r['endpoint_id'])}: {_line(r['error'])}")
            if r["changes"]:
                md += [
                    "| Severity | Change | Path | Before | After | Note |",
                    "|---|---|---|---|---|---|",
                ]
                for c in r["changes"]:
                    md.append(
                        f"| {ICONS.get(c['severity'], '')} {c['severity']} | {c['kind']} "
                        f"| {_code(c['path'])} | {_code(c.get('before'))} | {_code(c.get('after'))} "
                        f"| {_code(c.get('hint'))} |"
                    )
                    log.append(f"    {c['severity']:<8} {_line(c['message'])}")
            explanation = r.get("explanation")
            if explanation:
                told = [
                    f"What changed: {explanation['summary']}",
                    f"Impact: {explanation['impact']}",
                    f"Next step: {explanation['suggested_action']}",
                ]
                wrapped = "\n".join(textwrap.fill(_line(part), 100) for part in told)
                md += [
                    "",
                    "The explanation, as plain text:",
                    "",
                    *_fenced(wrapped, "text"),
                ]
            if notes:
                md += ["", *(f"- {_code(note)}" for note in notes)]
                log += [f"    note     {_line(note)}" for note in notes]

    print("\n".join(log))
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")


def notices_summary(
    results_path: str, code: str, summary_path: str, state: str = ""
) -> None:
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
    _new_state(state, "it has no record of the notices already handled", md, log)

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


def _data(value: Any) -> str:
    """The message of a workflow command: one line that cannot end the command early."""
    return _line(value).replace("%", "%25")


def _property(value: Any) -> str:
    """A property of a workflow command, such as a file name, which also ends at , and :."""
    return _data(value).replace(":", "%3A").replace(",", "%2C")


def _when(finding: dict[str, Any]) -> str:
    """When a change takes effect, from the scan's own dates."""
    day = str(finding.get("effective_at") or "")[:10]
    left = finding.get("days_left")
    if not day or not isinstance(left, int):
        return "no date"
    if left > 0:
        return f"{day}, in {left} day{'' if left == 1 else 's'}"
    return f"in effect since {day}"


def _level(finding: dict[str, Any]) -> str:
    """error: in effect or undated; warning: ahead; notice: too far away to count."""
    if not finding.get("due", True):
        return "notice"
    left = finding.get("days_left")
    return "warning" if isinstance(left, int) and left > 0 else "error"


def _report(results_path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(results_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _prefix(folder: str) -> str:
    """What turns a file's name under the scanned folder into its name in the repository."""
    parts = [
        part for part in folder.replace("\\", "/").split("/") if part not in ("", ".")
    ]
    return "/".join(parts) + "/" if parts else ""


def scan_summary(
    results_path: str,
    code: str,
    summary_path: str,
    fixes_path: str = "",
    folder: str = ".",
) -> None:
    """Print what `tiltmeter scan` found and, when asked, write it to the job summary.

    Each use becomes an annotation on its line, the first ten of each level. Headlines
    and lines of code are other people's text: code in the summary, one line in the log.
    ``folder`` is the scanned folder, which annotations need to name files from the root.
    """
    report = _report(results_path)
    prefix = _prefix(folder)
    verdict = SCAN_VERDICTS.get(int(code), f"Exit code {code}.")
    md = ["## Tiltmeter scan", "", f"**{verdict}** Exit code `{code}`.", ""]
    log = [f"Tiltmeter scan: {verdict} (exit code {code})"]
    if report.get("error"):
        md.append(f"> {_code(report['error'])}")
        log.append(f"  {_line(report['error'])}")

    affected = report.get("affected", [])
    annotated: dict[str, int] = {}
    if affected:
        md += ["| Provider | Change | Takes effect | Uses |", "|---|---|---|---|"]
        for finding in affected:
            counted = "" if finding.get("due", True) else " (does not count yet)"
            md.append(
                f"| {_code(finding.get('provider'))} | {_code(finding.get('headline'))} "
                f"| {_cell(_when(finding))}{counted} | {finding.get('count', 0)} |"
            )
        for finding in affected:
            level = _level(finding)
            md += ["", f"### {_code(finding.get('id'))}", ""]
            log.append(f"  {_line(finding.get('id'))}: {_when(finding)}")
            matches = finding.get("matches", [])
            for match in matches[:MAX_PLACES]:
                where = f"{prefix}{match.get('file')}:{match.get('line')}"
                md.append(
                    f"- {_code(where)} {_code(match.get('term'))}: {_code(match.get('detail'))}"
                )
            hidden = int(finding.get("count", 0)) - min(len(matches), MAX_PLACES)
            if hidden > 0:
                md.append(f"- and {hidden} more")
            for match in matches:
                if annotated.get(level, 0) >= MAX_ANNOTATIONS:
                    break
                annotated[level] = annotated.get(level, 0) + 1
                message = (
                    f"{match.get('term')}: {match.get('detail')}. "
                    f"{finding.get('headline')} ({_when(finding)})"
                )
                log.append(
                    f"::{level} file={_property(prefix + str(match.get('file')))},"
                    f"line={int(match.get('line') or 1)},title=Tiltmeter::{_data(message)}"
                )
            if finding.get("fix_command"):
                md += [
                    "",
                    "To fix it:",
                    "",
                    *_fenced(str(finding["fix_command"]), "text"),
                ]
    elif not report.get("error"):
        checked = report.get("entries", 0)
        md.append(f"Checked {checked} known API changes.")

    fixes = _fixes(fixes_path)
    if fixes:
        md += ["", "| Change | Result | Details |", "|---|---|---|"]
        for fix in fixes:
            result = FIX_RESULTS.get(fix.get("status", ""), fix.get("status", ""))
            detail = fix.get("detail") or ""
            if detail and detail == fix.get("pr_url"):
                detail = f"[{_cell(detail)}]({detail})"  # the URL gh returned
            else:
                detail = _code(detail)
            md.append(f"| {_code(fix.get('id'))} | {result} | {detail} |")
            log.append(
                f"  fix {_line(fix.get('id'))}: {result} {_line(fix.get('detail'))}"
            )

    print("\n".join(log))
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(md) + "\n")


def _fixes(fixes_path: str) -> list[dict[str, Any]]:
    if not fixes_path:
        return []
    try:
        data = json.loads(Path(fixes_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return (
        [item for item in data if isinstance(item, dict)]
        if isinstance(data, list)
        else []
    )


# A catalog id that can end a branch name: no "..", and no trailing dot or ".lock".
_BRANCH_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
BRANCH_PREFIX = "tiltmeter/scan-"


def _branch(notice_id: str) -> str | None:
    """The branch of the fix for a catalog entry, or None when its id cannot name one."""
    if not _BRANCH_ID.fullmatch(notice_id) or ".." in notice_id:
        return None
    if notice_id.endswith((".", ".lock")):
        return None
    return BRANCH_PREFIX + notice_id


def _remote_has(branch: str) -> bool:
    """Whether origin already has the branch, from an earlier run."""
    completed = subprocess.run(
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
        ],
        capture_output=True,
        check=False,
    )
    return completed.returncode == 0


def scan_fix(results_path: str, agent: str, out_path: str) -> None:
    """Open a pull request for each change the scan counted, one branch per change.

    A change whose branch is already on origin was handled by an earlier run, so a
    scheduled job opens each pull request once. Exits with 3 when a fix failed, 1 when an
    agent left something for a human, 0 otherwise.
    """
    done: list[dict[str, Any]] = []
    worst = 0
    for finding in _report(results_path).get("affected", []):
        notice_id = str(finding.get("id") or "")
        if not finding.get("due", True) or not finding.get("fix_command"):
            continue
        branch = _branch(notice_id)
        if branch is None:
            done.append(
                {
                    "id": notice_id,
                    "status": "skipped",
                    "detail": "its id cannot name a branch",
                }
            )
            continue
        if _remote_has(branch):
            done.append({"id": notice_id, "status": "exists", "detail": branch})
            continue
        command = [sys.executable, "-m", "tiltmeter", "fix", "--notice", notice_id]
        command += [
            "--allow-unsigned",
            "--repo",
            ".",
            "--yes",
            "--json",
            "--branch",
            branch,
        ]
        if agent:
            command += ["--agent", agent]
        completed = subprocess.run(
            command, stdout=subprocess.PIPE, text=True, encoding="utf-8", check=False
        )
        try:
            outcome = json.loads(completed.stdout)
        except ValueError:
            outcome = {}
        if not isinstance(outcome, dict):
            outcome = {}
        failed = completed.returncode not in (0, 1) or bool(outcome.get("error"))
        status = "failed" if failed else str(outcome.get("status") or "failed")
        detail = (
            outcome.get("error") or outcome.get("pr_url") or outcome.get("branch") or ""
        )
        if failed and not detail:
            detail = "see the step log"
        done.append(
            {
                "id": notice_id,
                "status": status,
                "detail": detail,
                "pr_url": outcome.get("pr_url"),
            }
        )
        worst = max(worst, 3 if failed else completed.returncode)
        print(f"Tiltmeter fix {_line(notice_id)}: {status} {_line(detail)}")
    Path(out_path).write_text(json.dumps(done), encoding="utf-8")
    if worst:
        raise SystemExit(worst)


MARKER = "<!-- tiltmeter-notice-draft -->"
DOCS = "https://github.com/tiltmeter-dev/tiltmeter/blob/main/docs/notices.md"


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
    elif command == "scan-summary":
        scan_summary(*args)
    elif command == "scan-fix":
        scan_fix(*args)
    elif command == "draft-comment":
        draft_comment(*args)
    elif command == "upsert-comment":
        upsert_comment(*args)
    else:
        raise SystemExit(f"unknown command: {command}")


if __name__ == "__main__":
    main()
