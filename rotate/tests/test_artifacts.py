import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout

from claude_rotate import artifacts
from claude_rotate.config import parse_config

NOW = 1_800_000_000.0
CONFIG = parse_config({"tiers": [
    {"name": "main", "config_dir": "/tmp/p", "auth": "login", "email": "you@example.com"},
    {"name": "spare", "config_dir": "/tmp/p", "auth": "token"},
    {"name": "work", "config_dir": None, "auth": "login", "email": "you@work.example", "share_artifacts": False},
]})
URL = "https://claude.ai/artifact/AbCdEf123"


def publish_payload(response=None, **tool_input):
    """Shaped like Claude Code's PostToolUse payload for the Artifact tool (fields seen in real transcripts)."""
    if response is None:
        response = {"url": URL, "path": "/tmp/page.html", "artifact_id": "0f0e0d0c-0000-4000-8000-000000000001",
                    "title": "Example Page", "updated": False, "owned": True, "icon": "chart", "audience": "owner",
                    "seq": 1, "version": "1800000000-abcd", "contract": "0.0.0", "liveSubscription": "arming"}
    return {"session_id": "s1", "transcript_path": "/t/s1.jsonl", "cwd": "/home/you/projects/demo",
            "hook_event_name": "PostToolUse", "tool_name": "Artifact", "tool_use_id": "toolu_01",
            "tool_input": tool_input or {"file_path": "/tmp/page.html", "icon": "chart"},
            "tool_response": response}


class PublishedTest(unittest.TestCase):
    def test_reads_a_page_publish(self):
        self.assertEqual(artifacts.published(publish_payload()),
                         {"url": URL, "id": "0f0e0d0c-0000-4000-8000-000000000001", "title": "Example Page"})

    def test_created_from_a_type_has_no_id(self):
        resp = {"created_from_type": True, "url": URL, "title": "Deck", "type": {"url": "https://claude.ai/artifact/Type1"}}
        self.assertEqual(artifacts.published(publish_payload(resp, type_url="https://claude.ai/artifact/Type1")),
                         {"url": URL, "id": None, "title": "Deck"})

    def test_text_result_is_parsed_too(self):
        text = "Published /tmp/page.html at %s (Version 1, version id 1800000000-abcd)" % URL
        for resp in (text, [{"type": "text", "text": text}], {"content": [{"type": "text", "text": text}]}):
            self.assertEqual(artifacts.published(publish_payload(resp, title="T"))["url"], URL, resp)
        self.assertEqual(artifacts.published(publish_payload(text + "?sk=key"))["url"], URL)

    def test_ignores_other_actions_assets_and_failures(self):
        ignored = [
            publish_payload(action="list"), publish_payload(action="read", url=URL),
            publish_payload(action="open", url=URL), publish_payload(action="delete", url=URL),
            publish_payload({"asset_uploads": [], "owned": True, "written": 1}, url=URL, file_path="/x.png", asset=True),
            publish_payload("Error: `edits` changes only the file at `file_path`. Nothing was published."),
            publish_payload("<tool_use_error>refused for %s</tool_use_error>" % URL),
            publish_payload({"url": URL, "owned": False, "title": "Someone else's"}),
            publish_payload({"warnings": ["nothing"]}),
            {"tool_input": "junk", "tool_response": 7}, {},
        ]
        for payload in ignored:
            self.assertIsNone(artifacts.published(payload), payload)


class HandlePublishTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def lines(self):
        with open(artifacts.ledger_path(self.dir)) as f:
            return [json.loads(line) for line in f]

    def test_extra_account_is_recorded_and_flagged(self):
        note = artifacts.handle_publish(publish_payload(), {"RCLAUDE_ACCOUNT": "spare"}, CONFIG, self.dir, NOW)
        self.assertIn("spare", note)
        self.assertIn("rclaude artifacts", note)
        self.assertEqual(self.lines(), [{
            "t": NOW, "event": "publish", "url": URL, "id": "0f0e0d0c-0000-4000-8000-000000000001",
            "title": "Example Page", "account": "spare", "main": False, "session": "s1",
            "cwd": "/home/you/projects/demo"}])
        self.assertEqual(stat.S_IMODE(os.stat(artifacts.ledger_path(self.dir)).st_mode), 0o600)

    def test_main_account_needs_no_share(self):
        self.assertIsNone(artifacts.handle_publish(publish_payload(), {"RCLAUDE_ACCOUNT": "main"}, CONFIG, self.dir, NOW))
        self.assertTrue(self.lines()[0]["main"])

    def test_excluded_account_is_recorded_but_not_flagged(self):
        self.assertIsNone(artifacts.handle_publish(publish_payload(), {"RCLAUDE_ACCOUNT": "work"}, CONFIG, self.dir, NOW))
        self.assertEqual((self.lines()[0]["main"], self.lines()[0]["share"]), (False, False))

    def test_nothing_recorded_without_an_account_or_a_publish(self):
        self.assertIsNone(artifacts.handle_publish(publish_payload(), {}, CONFIG, self.dir, NOW))
        self.assertIsNone(artifacts.handle_publish(publish_payload(action="list"), {"RCLAUDE_ACCOUNT": "spare"},
                                                   CONFIG, self.dir, NOW))
        self.assertFalse(os.path.exists(artifacts.ledger_path(self.dir)))


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def publish(self, url, account, title="T", t=NOW, cwd="/home/you/projects/demo"):
        artifacts.append(self.dir, {"t": t, "event": "publish", "url": url, "id": None, "title": title,
                                    "account": account, "main": account == "main", "session": "s", "cwd": cwd})

    def test_fold_latest_publish_wins_and_shared_sticks(self):
        self.publish(URL, "spare", "Old")
        artifacts.append(self.dir, {"t": NOW, "event": "shared", "url": URL})
        self.publish(URL, "spare", "New", t=NOW + 5)
        with open(artifacts.ledger_path(self.dir), "a") as f:
            f.write("not json\n" + json.dumps({"event": "comment", "url": URL}) + "\n")
        artifacts.append(self.dir, {"t": NOW, "event": "shared", "url": "https://claude.ai/artifact/Unknown"})
        records = artifacts.fold(self.dir)
        self.assertEqual(len(records), 1)
        self.assertEqual((records[0]["title"], records[0]["t"], records[0]["shared"]), ("New", NOW + 5, True))

    def test_share_targets_skip_the_owner_excluded_accounts_and_the_same_email(self):
        self.assertEqual([t.name for t in artifacts.share_targets(CONFIG, "spare")], ["main"])
        self.assertEqual(artifacts.share_targets(CONFIG, "work"), [])
        same = parse_config({"tiers": [{"name": "main", "config_dir": None, "auth": "login", "email": "you@example.com"},
                                       {"name": "alias", "config_dir": None, "auth": "token", "email": "You@example.com"}]})
        self.assertEqual(artifacts.share_targets(same, "alias"), [])

    def run_command(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = artifacts.command(list(args), CONFIG, self.dir)
        return code, out.getvalue()

    def test_list_groups_pending_by_account_with_targets(self):
        self.publish("https://claude.ai/artifact/One", "main", "Main page")
        self.publish("https://claude.ai/artifact/Two", "spare", "Spare page")
        self.publish("https://claude.ai/artifact/Three", "work", "Work page")
        code, out = self.run_command()
        self.assertEqual(code, 0)
        self.assertIn("spare: share with main (you@example.com)", out)
        self.assertIn("  2  Spare page  https://claude.ai/artifact/Two", out)
        self.assertIn("demo", out)
        self.assertNotIn("Main page", out)
        self.assertNotIn("Work page", out)
        code, out = self.run_command("--all")
        for expected in ("Main page", "main account", "Spare page", "to share", "Work page", "not shared"):
            self.assertIn(expected, out)

    def test_mark_shared_by_number_url_and_all(self):
        for i, name in enumerate(("One", "Two", "Three")):
            self.publish("https://claude.ai/artifact/" + name, "spare", name)
        self.assertEqual(self.run_command("shared", "1")[0], 0)
        self.assertEqual(self.run_command("shared", "https://claude.ai/artifact/Two?x=1")[0], 0)
        self.assertEqual([r["shared"] for r in artifacts.fold(self.dir)], [True, True, False])
        self.assertEqual(self.run_command("shared", "all"), (0, "Marked 1 as shared.\n"))
        self.assertEqual(self.run_command(), (0, "Nothing to share.\n"))
        self.assertEqual(self.run_command("shared", "9")[0], 1)
        self.assertEqual(self.run_command("shared", "https://claude.ai/artifact/Nope")[0], 1)

    def test_empty_ledger_and_help(self):
        self.assertEqual(self.run_command(), (0, "No artifacts recorded yet.\n"))
        code, out = self.run_command("--help")
        self.assertEqual(code, 0)
        self.assertIn("usage: rclaude artifacts", out)
        self.assertEqual(self.run_command("bogus")[0], 2)
