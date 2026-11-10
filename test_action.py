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
        for mode in ("check", "notices"):
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


if __name__ == "__main__":
    unittest.main()
