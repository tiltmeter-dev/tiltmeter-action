"""Tests for action.py: python -m unittest test_action"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

import action

HERE = Path(__file__).parent


def rendered(markdown: str) -> str:
    """What GitHub renders of a summary: everything outside code blocks and code spans."""
    kept, fenced = [], False
    for line in markdown.splitlines():
        if line.startswith(action.FENCE):
            fenced = not fenced
        elif not fenced:
            kept.append(" ".join(line.split("`")[::2]))
    assert not fenced
    return "\n".join(kept)


def summarize(
    command: str, data: object, code: str, state: str = ""
) -> tuple[str, str]:
    """The job summary and the log that ``summary`` or ``notices-summary`` write."""
    with tempfile.TemporaryDirectory() as folder:
        results = Path(folder) / "results.json"
        results.write_text(json.dumps(data), encoding="utf-8")
        summary = Path(folder) / "summary.md"
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            getattr(action, command)(str(results), code, str(summary), state)
        return summary.read_text(encoding="utf-8"), log.getvalue()


def drifted(**extra: object) -> dict[str, object]:
    return {
        "endpoint_id": "billing",
        "status_code": 200,
        "outcome": "drift",
        "max_severity": "breaking",
        "error": None,
        "changes": [
            {
                "severity": "breaking",
                "kind": "field_removed",
                "path": "$.phone",
                "before": "string",
                "after": None,
                "hint": "possibly renamed to $.phone_number",
                "message": "Field $.phone (string) is no longer returned.",
            }
        ],
        **extra,
    }


class CheckSummary(unittest.TestCase):
    def test_a_drifted_endpoint_gets_its_table(self) -> None:
        clean = {
            "endpoint_id": "users",
            "status_code": 200,
            "outcome": "clean",
            "changes": [],
        }
        md, log = summarize("summary", [drifted(), clean], "2")
        self.assertIn("**Breaking drift.** Exit code `2`.", md)
        self.assertIn("| `billing` | 200 | 🔴 breaking | 1 |", md)
        self.assertIn("| `users` | 200 | clean | 0 |", md)
        self.assertIn(
            "| 🔴 breaking | field_removed | `$.phone` | `string` |  "
            "| `possibly renamed to $.phone_number` |",
            md,
        )
        self.assertEqual(md.count("### "), 1)  # nothing to say about a clean endpoint
        self.assertIn("    breaking Field $.phone (string) is no longer returned.", log)

    def test_text_from_the_api_cannot_render_or_start_a_command(self) -> None:
        # Keys are free text, and an explanation is written by a model that read them.
        hostile = drifted(
            error="unexpected <img src=https://evil.test/e>",
            explanation={
                "summary": "See [docs](https://evil.test/d) ````\n\n## Approved by @octocat",
                "impact": "<img src=https://evil.test/i>",
                "suggested_action": "Click https://evil.test/a",
            },
            notes=["LLM explanation failed: ![n](https://evil.test/n)"],
        )
        change = hostile["changes"][0]  # type: ignore[index]
        change["path"] = '$["y` ![t](https://evil.test/p.png) `z"]'
        change["hint"] = 'possibly renamed to $["<a href=https://evil.test/h>ok</a>"]'
        change["message"] = "New field x\n::error title=Blocked::pwned"
        md, log = summarize("summary", [hostile], "2")
        shown = rendered(md)
        for live in ("evil.test", "@octocat", "<img", "<a href", "## Approved"):
            self.assertIn(live, md)
            self.assertNotIn(live, shown)
        # The only headings are the action's own: the summary's and the endpoint's.
        headings = [line.strip() for line in shown.splitlines() if line.startswith("#")]
        self.assertEqual(headings, ["## Tiltmeter", "###"])
        self.assertFalse([line for line in log.splitlines() if line.startswith("::")])

    def test_notes_are_shown_even_without_changes(self) -> None:
        noted = {
            "endpoint_id": "users",
            "status_code": 200,
            "outcome": "clean",
            "changes": [],
            "notes": ["slack notification failed: HTTP 404"],
        }
        md, log = summarize("summary", [noted], "0")
        self.assertIn("### `users`", md)
        self.assertIn("- `slack notification failed: HTTP 404`", md)
        self.assertIn("    note     slack notification failed: HTTP 404", log)

    def test_a_run_that_printed_no_results_still_gets_its_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            summary = Path(folder) / "summary.md"
            with contextlib.redirect_stdout(io.StringIO()):
                action.summary(str(Path(folder) / "missing.json"), "3", str(summary))
            self.assertIn(
                "**A check could not run.**", summary.read_text(encoding="utf-8")
            )


class EmptyCache(unittest.TestCase):
    def test_a_run_that_starts_over_says_so(self) -> None:
        clean = [
            {
                "endpoint_id": "users",
                "status_code": 200,
                "outcome": "baseline",
                "changes": [],
            }
        ]
        md, log = summarize("summary", clean, "0", "new")
        self.assertIn("> Nothing was restored from the Actions cache", md)
        self.assertIn("it recorded a new baseline and could not report drift", md)
        self.assertIn("::notice title=Tiltmeter::Nothing was restored", log)
        md, log = summarize("notices_summary", {"feeds": [], "applied": []}, "0", "new")
        self.assertIn("it has no record of the notices already handled", md)
        self.assertIn("::notice title=Tiltmeter::Nothing was restored", log)

    def test_a_restored_baseline_says_nothing(self) -> None:
        for command, data in (
            ("summary", [drifted()]),
            ("notices_summary", {"feeds": []}),
        ):
            md, log = summarize(command, data, "0")
            self.assertNotIn("Nothing was restored", md + log)


class StateDir(unittest.TestCase):
    def located(self, folder: str, text: str | None) -> str:
        config = Path(folder) / "ci" / "tiltmeter.yaml"
        config.parent.mkdir(exist_ok=True)
        if text is not None:
            config.write_text(text, encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            action.state_dir(str(config))
        self.assertTrue(out.getvalue().startswith("dir="))
        return out.getvalue().removeprefix("dir=").strip()

    def test_it_follows_the_config_like_tiltmeter_does(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            beside = os.path.join(folder, "ci", ".tiltmeter")
            self.assertEqual(self.located(folder, "endpoints: []\n"), beside)
            self.assertEqual(self.located(folder, None), beside)  # no config yet
            self.assertEqual(self.located(folder, "endpoints: [\n"), beside)  # not YAML
            self.assertEqual(
                self.located(folder, "state_dir: ../state\n"),
                os.path.join(folder, "state"),
            )
            elsewhere = os.path.join(folder, "elsewhere")
            self.assertEqual(
                self.located(folder, f"state_dir: {json.dumps(elsewhere)}\n"), elsewhere
            )


def step(name: str) -> str:
    """The script of one of the action's steps."""
    steps = yaml.safe_load((HERE / "action.yml").read_text(encoding="utf-8"))["runs"][
        "steps"
    ]
    return next(s["run"] for s in steps if s.get("name") == name)


class FailOn(unittest.TestCase):
    def code(self, mode: str, fail_on: str, code: int) -> int:
        env = {**os.environ, "MODE": mode, "FAIL_ON": fail_on, "CODE": str(code)}
        done = subprocess.run(
            ["bash", "-c", step("Apply fail-on")], env=env, check=False
        )
        return done.returncode

    def test_drift_fails_the_step_as_asked(self) -> None:
        for mode in ("check", "notices", "scan"):
            self.assertEqual(
                [self.code(mode, "warning", c) for c in range(5)], [0, 1, 2, 3, 4]
            )
            self.assertEqual(
                [self.code(mode, "breaking", c) for c in range(5)], [0, 0, 2, 0, 4]
            )
            self.assertEqual(
                [self.code(mode, "never", c) for c in range(5)], [0, 0, 0, 0, 4]
            )
        self.assertEqual(self.code("check", "sometimes", 0), 4)

    def test_the_provider_modes_fail_whatever_fail_on_says(self) -> None:
        # Exit code 1 is a pull request that changes responses without a notice.
        for fail_on in ("warning", "breaking", "never"):
            self.assertEqual(self.code("notice-draft", fail_on, 1), 1)
            self.assertEqual(self.code("notice-publish", fail_on, 4), 4)
            self.assertEqual(self.code("notice-draft", fail_on, 0), 0)


class DraftComment(unittest.TestCase):
    def write(self, folder: str, draft: object) -> str:
        path = Path(folder) / "draft.json"
        path.write_text(
            json.dumps(draft) if draft is not None else "", encoding="utf-8"
        )
        return str(path)

    def test_a_draft_becomes_a_table_and_a_code_block(self) -> None:
        draft = {
            "id": "2026-11-03-customers",
            "endpoints": [
                {
                    "method": "GET",
                    "url": "https://api.acme.example/v1/customers",
                    "changes": [
                        {
                            "path": "$.phone",
                            "kind": "field_removed",
                            "renamed_to": "$.phone_number",
                        },
                        {
                            "path": "$.balance",
                            "kind": "type_changed",
                            "before": "integer",
                            "after": "`string` @team",
                        },
                    ],
                }
            ],
            "summary": "Has ```` inside",
        }
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "comment.md"
            action.draft_comment(
                self.write(folder, draft), "openapi.yaml", "notices/", str(out)
            )
            body = out.read_text(encoding="utf-8")
        self.assertTrue(body.startswith(action.MARKER))
        self.assertIn("save this draft as `notices/2026-11-03-customers.json`", body)
        self.assertIn(
            "| `field_removed` | `$.phone` | — | renamed to `$.phone_number` |", body
        )
        self.assertIn(
            "| `type_changed` | `$.balance` | `integer` | `'string' @team` |", body
        )
        self.assertEqual(body.count("````"), 2)  # the summary cannot close the block

    def test_no_draft_means_no_change(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / "comment.md"
            action.draft_comment(
                self.write(folder, None), "openapi.yaml", "notices", str(out)
            )
            body = out.read_text(encoding="utf-8")
        self.assertIn("changes no responses in `openapi.yaml`", body)
        self.assertNotIn("save this draft as", body)


class Login(unittest.TestCase):
    def test_tokens_without_a_user_comment_as_the_actions_bot(self) -> None:
        original = action.subprocess.run

        def fake(command: list[str], **_: object) -> object:
            class Done:
                returncode = 1
                stdout = ""

            return Done()

        action.subprocess.run = fake  # type: ignore[assignment]
        try:
            self.assertEqual(action._login(), "github-actions[bot]")
        finally:
            action.subprocess.run = original


class NoticesSummary(unittest.TestCase):
    def test_provider_text_cannot_start_a_workflow_command(self) -> None:
        data = {
            "feeds": [
                {
                    "subscription": "x.test",
                    "error": None,
                    "entries": [
                        {
                            "state": "rejected",
                            "id": "n",
                            "problem": "bad\n::error::pwned",
                        }
                    ],
                }
            ],
            "applied": [
                {
                    "provider": "x.test",
                    "notice_id": "n2",
                    "status": "opened",
                    "detail": "https://github.test/pr/1",
                    "outcome": {"pr_url": "https://github.test/pr/1"},
                },
                {
                    "provider": "x.test",
                    "notice_id": "n3",
                    "status": "unaffected",
                    "detail": "[x](https://evil.test)",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as folder:
            results = Path(folder) / "results.json"
            results.write_text(json.dumps(data), encoding="utf-8")
            summary = Path(folder) / "summary.md"
            action.notices_summary(str(results), "0", str(summary))
            text = summary.read_text(encoding="utf-8")
        self.assertIn("[https://github.test/pr/1](https://github.test/pr/1)", text)
        self.assertIn("`[x](https://evil.test)`", text)
        self.assertNotIn("\n::error::", text)


def finding(**extra: object) -> dict[str, object]:
    """A catalog entry the scanned folder uses, as `tiltmeter scan --json` reports it."""
    return {
        "id": "groq-retired-models-2026-08",
        "provider": "api.groq.com",
        "headline": "Groq no longer serves 16 models it retired.",
        "effective_at": "2026-08-16T00:00:00Z",
        "days_left": -87,
        "due": True,
        "count": 1,
        "matches": [
            {
                "file": "app/llm.py",
                "line": 10,
                "term": "llama3-8b-8192",
                "detail": "retired; use openai/gpt-oss-20b",
            }
        ],
        "fix_command": "tiltmeter fix --notice groq-retired-models-2026-08 --allow-unsigned --repo .",
        **extra,
    }


def scanned(
    report: object, code: str, fixes: object = None, folder: str = "."
) -> tuple[str, str]:
    """The job summary and the log that ``scan-summary`` writes."""
    with tempfile.TemporaryDirectory() as tmp:
        results = Path(tmp) / "scan.json"
        results.write_text(json.dumps(report), encoding="utf-8")
        fixes_path = ""
        if fixes is not None:
            fixes_path = str(Path(tmp) / "fixes.json")
            Path(fixes_path).write_text(json.dumps(fixes), encoding="utf-8")
        summary = Path(tmp) / "summary.md"
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            action.scan_summary(str(results), code, str(summary), fixes_path, folder)
        return summary.read_text(encoding="utf-8"), log.getvalue()


ANNOTATION = r"::(error|warning|notice) file=[^,\n]+,line=\d+,title=Tiltmeter::[^\n]*"


class ScanSummary(unittest.TestCase):
    def test_a_use_gets_a_row_a_place_and_an_annotation(self) -> None:
        md, log = scanned({"affected": [finding()], "entries": 15}, "1")
        self.assertIn("**The code uses a known API change.** Exit code `1`.", md)
        self.assertIn(
            "| `api.groq.com` | `Groq no longer serves 16 models it retired.` "
            "| in effect since 2026-08-16 | 1 |",
            md,
        )
        self.assertIn(
            "- `app/llm.py:10` `llama3-8b-8192`: `retired; use openai/gpt-oss-20b`", md
        )
        self.assertIn("tiltmeter fix --notice groq-retired-models-2026-08", md)
        self.assertIn(
            "::error file=app/llm.py,line=10,title=Tiltmeter::llama3-8b-8192: retired; "
            "use openai/gpt-oss-20b. Groq no longer serves 16 models it retired. "
            "(in effect since 2026-08-16)",
            log.splitlines(),
        )

    def test_files_are_named_from_the_repository_root(self) -> None:
        for folder in ("services/api", "./services/api/", "services\\api"):
            md, log = scanned({"affected": [finding()]}, "1", folder=folder)
            self.assertIn("::error file=services/api/app/llm.py,line=10,", log)
            self.assertIn("- `services/api/app/llm.py:10`", md)

    def test_a_change_ahead_warns_and_one_too_far_only_notes(self) -> None:
        ahead = finding(id="soon", days_left=24, effective_at="2026-12-04T00:00:00Z")
        far = finding(id="far", days_left=400, due=False)
        md, log = scanned({"affected": [finding(), ahead, far]}, "1")
        levels = [
            line.split(" ", 1)[0] for line in log.splitlines() if line[:2] == "::"
        ]
        self.assertEqual(levels, ["::error", "::warning", "::notice"])
        self.assertIn("| 2026-12-04, in 24 days | 1 |", md)
        self.assertIn("(does not count yet) | 1 |", md)
        self.assertIn("(2026-12-04, in 24 days)", log)

    def test_annotations_stop_at_ten_of_a_level(self) -> None:
        match = finding()["matches"][0]  # type: ignore[index]
        many = [{**match, "line": line} for line in range(1, 31)]
        md, log = scanned({"affected": [finding(matches=many, count=30)]}, "1")
        self.assertEqual(log.count("::error file="), 10)
        self.assertEqual(md.count("- `app/llm.py:"), 10)
        self.assertIn("- and 20 more", md)

    def test_other_peoples_text_cannot_render_or_start_a_command(self) -> None:
        # A headline is a provider's, a line of code and a file name are the repository's.
        hostile = finding(
            id="x` ![i](https://evil.test/i.png) `y",
            headline="See [docs](https://evil.test/d)\n::error::pwned\n## Approved by @octocat",
            matches=[
                {
                    "file": "a,line=1::x\n::error::pwned.py",
                    "line": 3,
                    "term": "<img src=https://evil.test/t>",
                    "detail": "50% off, 100%25 real\r\n::warning::pwned",
                }
            ],
            fix_command="tiltmeter fix ````\n\n## Approved",
        )
        md, log = scanned({"affected": [hostile], "error": "bad\n::error::pwned"}, "1")
        shown = rendered(md)
        for live in ("evil.test", "@octocat", "<img", "## Approved"):
            self.assertIn(live, md)
            self.assertNotIn(live, shown)
        headings = [line.strip() for line in shown.splitlines() if line.startswith("#")]
        self.assertEqual(headings, ["## Tiltmeter scan", "###"])
        commands = [line for line in log.splitlines() if line.startswith("::")]
        self.assertEqual(len(commands), 1)
        self.assertRegex(commands[0], f"^{ANNOTATION}$")
        # The file name ends where the action says, and a % is no escape of the text's own.
        self.assertIn(
            "::error file=a%2Cline=1%3A%3Ax %3A%3Aerror%3A%3Apwned.py,line=3,", log
        )
        self.assertIn("50%25 off, 100%2525 real ::warning::pwned.", commands[0])

    def test_a_clean_scan_says_how_much_it_checked(self) -> None:
        md, log = scanned({"affected": [], "entries": 15}, "0")
        self.assertIn("**The code uses no known API change.** Exit code `0`.", md)
        self.assertIn("Checked 15 known API changes.", md)
        self.assertNotIn("::", log)

    def test_a_scan_that_printed_nothing_still_gets_its_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            summary = Path(tmp) / "summary.md"
            with contextlib.redirect_stdout(io.StringIO()):
                action.scan_summary(str(Path(tmp) / "missing.json"), "4", str(summary))
            self.assertIn("Configuration or usage error", summary.read_text("utf-8"))

    def test_fixes_are_listed_with_the_link_gh_returned(self) -> None:
        fixes = [
            {
                "id": "groq-retired-models-2026-08",
                "status": "opened",
                "detail": "https://github.test/pr/7",
                "pr_url": "https://github.test/pr/7",
            },
            {"id": "other", "status": "exists", "detail": "tiltmeter/scan-other"},
            {"id": "bad", "status": "failed", "detail": "[x](https://evil.test)"},
        ]
        md, log = scanned({"affected": [finding()]}, "1", fixes)
        self.assertIn(
            "| `groq-retired-models-2026-08` | pull request opened "
            "| [https://github.test/pr/7](https://github.test/pr/7) |",
            md,
        )
        self.assertIn("| `other` | already has a branch | `tiltmeter/scan-other` |", md)
        self.assertIn("| `bad` | failed | `[x](https://evil.test)` |", md)
        self.assertIn("  fix other: already has a branch tiltmeter/scan-other", log)


class ScanFix(unittest.TestCase):
    def fixed(
        self,
        report: object,
        *,
        agent: str = "",
        on_remote: tuple[str, ...] = (),
        printed: object = None,
        code: int = 0,
    ) -> tuple[list[dict[str, object]], int, list[list[str]]]:
        """What ``scan-fix`` wrote, its exit code and the `tiltmeter fix` commands it ran."""
        ran: list[list[str]] = []

        def fake(command: list[str], **_: object) -> object:
            class Done:
                returncode = 0
                stdout = ""

            done = Done()
            if command[:2] == ["git", "ls-remote"]:
                branch = command[-1].removeprefix("refs/heads/")
                done.returncode = 0 if branch in on_remote else 2
            else:
                ran.append(command)
                done.returncode = code
                done.stdout = (
                    printed if isinstance(printed, str) else json.dumps(printed)
                )
            return done

        original = action.subprocess.run
        action.subprocess.run = fake  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                results = Path(tmp) / "scan.json"
                results.write_text(json.dumps(report), encoding="utf-8")
                out = Path(tmp) / "fixes.json"
                exit_code = 0
                with contextlib.redirect_stdout(io.StringIO()):
                    try:
                        action.scan_fix(str(results), agent, str(out))
                    except SystemExit as stop:
                        exit_code = int(stop.code or 0)
                return json.loads(out.read_text(encoding="utf-8")), exit_code, ran
        finally:
            action.subprocess.run = original

    def test_each_change_that_counts_gets_one_fix_on_its_own_branch(self) -> None:
        outcome = {"status": "opened", "pr_url": "https://github.test/pr/7"}
        done, code, ran = self.fixed({"affected": [finding()]}, printed=outcome)
        self.assertEqual(code, 0)
        self.assertEqual(
            done,
            [
                {
                    "id": "groq-retired-models-2026-08",
                    "status": "opened",
                    "detail": "https://github.test/pr/7",
                    "pr_url": "https://github.test/pr/7",
                }
            ],
        )
        [command] = ran
        self.assertEqual(command[1:4], ["-m", "tiltmeter", "fix"])
        self.assertEqual(
            command[4:],
            [
                "--notice",
                "groq-retired-models-2026-08",
                "--allow-unsigned",
                "--repo",
                ".",
                "--yes",
                "--json",
                "--branch",
                "tiltmeter/scan-groq-retired-models-2026-08",
            ],
        )
        _, _, ran = self.fixed(
            {"affected": [finding()]}, agent="codex", printed=outcome
        )
        self.assertEqual(ran[0][-2:], ["--agent", "codex"])

    def test_a_change_with_a_branch_on_origin_is_left_alone(self) -> None:
        branch = "tiltmeter/scan-groq-retired-models-2026-08"
        done, code, ran = self.fixed({"affected": [finding()]}, on_remote=(branch,))
        self.assertEqual((code, ran), (0, []))
        self.assertEqual(
            done,
            [
                {
                    "id": "groq-retired-models-2026-08",
                    "status": "exists",
                    "detail": branch,
                }
            ],
        )

    def test_what_does_not_count_or_cannot_be_fixed_is_not_tried(self) -> None:
        report = {
            "affected": [
                finding(id="far", due=False),
                finding(id="no-command", fix_command=None),
                finding(id="a..b"),
                finding(id="ends.lock"),
                finding(id="-x"),
                finding(id="x y; rm -rf"),
            ]
        }
        done, code, ran = self.fixed(report, printed={"status": "opened"})
        self.assertEqual((code, ran), (0, []))
        self.assertEqual(
            [(item["id"], item["status"]) for item in done],
            [(name, "skipped") for name in ("a..b", "ends.lock", "-x", "x y; rm -rf")],
        )

    def test_a_fix_that_fails_is_worse_than_questions_for_a_human(self) -> None:
        report = {"affected": [finding()]}
        blocked, code, _ = self.fixed(report, printed={"status": "blocked"}, code=1)
        self.assertEqual((blocked[0]["status"], code), ("blocked", 1))
        failed, code, _ = self.fixed(report, printed="error: no API key", code=4)
        self.assertEqual(
            (failed[0]["status"], failed[0]["detail"], code),
            ("failed", "see the step log", 3),
        )
        said = {"status": "unchanged", "error": "push rejected\n::error::pwned"}
        failed, code, _ = self.fixed(report, printed=said, code=3)
        self.assertEqual((failed[0]["status"], code), ("failed", 3))


class Modes(unittest.TestCase):
    def test_an_unknown_mode_names_the_ones_there_are(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                **os.environ,
                "MODE": "scna",
                "RUNNER_TEMP": tmp,
                "PACKAGE": "tiltmeter",
            }
            done = subprocess.run(
                ["bash", "-c", step("Run Tiltmeter")],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(done.returncode, 4)
        self.assertIn(
            "mode must be check, scan, notices, notice-draft or notice-publish",
            done.stdout,
        )

    def test_scan_needs_no_baseline(self) -> None:
        steps = yaml.safe_load((HERE / "action.yml").read_text(encoding="utf-8"))[
            "runs"
        ]["steps"]
        for name in (
            "Locate the baseline",
            "Restore the baseline",
            "Save the baseline",
        ):
            condition = next(s["if"] for s in steps if s.get("name") == name)
            self.assertIn("inputs.mode != 'scan'", condition, name)


if __name__ == "__main__":
    unittest.main()
