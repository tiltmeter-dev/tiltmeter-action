"""Tests for action.py: python -m unittest test_action"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import action


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
