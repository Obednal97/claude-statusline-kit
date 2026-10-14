import calendar
import json
import os
import tempfile
import time
import unittest
from datetime import datetime

from claude_rotate.transcript import login_since, trailing_limit

RESET = 1792123200.0  # Oct 16 2026 05:00 Europe/London, from the real error captured on 2026-10-11
NOW = datetime(2026, 10, 11, 16, 30).timestamp()
REAL_MSG = ("You've hit your monthly spend limit · raise it at https://claude.ai/settings/usage?from=cc_cli_limit_message"
            " · your weekly limit resets Oct 16 at 5am (Europe/London)")
ERR_TS = "2026-10-11T15:08:35.343Z"


def cred(org, ts="2026-10-11T15:08:34.000Z"):
    return {"type": "attachment", "timestamp": ts, "attachment": {"type": "credential_org", "organizationUuid": org}}


def user(text="done", ts="2026-10-11T15:08:34.100Z"):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


def limit_error(resets_at=RESET, ts=ERR_TS, text=REAL_MSG, quota=True):
    e = {"type": "assistant", "timestamp": ts, "isApiErrorMessage": True, "apiError": "usage_limit_reached",
         "error": "rate_limit", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    if quota:
        e["quotaLimits"] = {"status": "rejected", "resetsAt": resets_at, "rateLimitType": "seven_day"}
    return e


def reply(text="PONG", ts="2026-10-11T15:20:00.000Z"):
    return {"type": "assistant", "timestamp": ts, "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def overloaded(ts="2026-10-11T15:21:00.000Z"):
    return {"type": "assistant", "timestamp": ts, "isApiErrorMessage": True, "apiError": "overloaded",
            "error": "overloaded", "message": {"content": [{"type": "text", "text": "API Error: Overloaded"}]}}


class TrailingLimitTest(unittest.TestCase):
    def write(self, entries, filler=0):
        path = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        with open(path, "w") as f:
            for i, e in enumerate(entries):
                f.write(json.dumps(e) + "\n")
                if i == 0 and filler:  # push the first entry out of a small tail window
                    for _ in range(filler):
                        f.write(json.dumps({"type": "file-history-snapshot", "pad": "x" * 100}) + "\n")
        return path

    def test_limit_at_the_end_is_reported_with_its_org(self):
        # Claude Code writes the credential_org attachment right after the prompt it applies to
        found = trailing_limit(self.write([user(), cred("org-a"), limit_error()]), NOW)
        self.assertEqual(found["resets_at"], RESET)
        self.assertEqual(found["org"], "org-a")
        self.assertEqual(found["at"], calendar.timegm(time.strptime("2026-10-11T15:08:35", "%Y-%m-%dT%H:%M:%S")))

    def test_a_reply_after_the_limit_clears_it(self):
        self.assertIsNone(trailing_limit(self.write([user(), cred("org-a"), limit_error(), user("go"), reply()]), NOW))

    def test_other_api_errors_do_not_clear_it(self):
        self.assertIsNotNone(trailing_limit(self.write([user(), cred("org-a"), limit_error(), overloaded()]), NOW))

    def test_expired_limit_is_ignored(self):
        self.assertIsNone(trailing_limit(self.write([user(), cred("org-a"), limit_error(resets_at=NOW - 1)]), NOW))

    def test_token_session_has_no_org(self):
        self.assertIsNone(trailing_limit(self.write([user(), limit_error()]), NOW)["org"])

    def test_reset_comes_from_the_message_when_quota_is_missing(self):
        found = trailing_limit(self.write([user(), cred("org-a"), limit_error(quota=False)]), NOW)
        self.assertEqual(found["resets_at"], datetime(2026, 10, 16, 5, 0).timestamp())

    def test_org_of_the_failing_prompt_wins(self):
        entries = [user(), cred("org-a"), reply(), user(), cred("org-b", ts="2026-10-11T15:30:00Z"), limit_error()]
        self.assertEqual(trailing_limit(self.write(entries), NOW)["org"], "org-b")

    def test_org_is_not_carried_over_to_a_later_prompt(self):
        # login turn on a, then (after a relaunch onto a token) a prompt with no credential_org hits b's limit
        entries = [user(), cred("org-a"), reply(), user("next"), limit_error()]
        self.assertIsNone(trailing_limit(self.write(entries), NOW)["org"])

    def test_tool_results_do_not_reset_the_prompts_org(self):
        tool_result = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}}
        entries = [user(), cred("org-a"), reply(), tool_result, limit_error()]
        self.assertEqual(trailing_limit(self.write(entries), NOW)["org"], "org-a")

    def test_missing_file(self):
        self.assertIsNone(trailing_limit("/nonexistent/t.jsonl", NOW))


def login_entries(ts):
    # what Claude Code writes for a /login typed in the session (three entries, same timestamp)
    return [
        {"type": "user", "timestamp": ts, "isMeta": True, "message": {"content": "<local-command-caveat>The command below was run directly in Claude Code</local-command-caveat>"}},
        {"type": "user", "timestamp": ts, "message": {"content": "<command-name>/login</command-name>\n<command-message>login</command-message>"}},
        {"type": "user", "timestamp": ts, "message": {"content": "<local-command-stdout>Login successful</local-command-stdout>"}},
    ]


class LoginSinceTest(unittest.TestCase):
    def write(self, entries):
        path = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return path

    def at(self, ts):
        return calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%S"))

    def test_login_after_launch_is_found(self):
        path = self.write([user()] + login_entries("2026-10-11T16:00:30.000Z") + [user("continue")])
        self.assertTrue(login_since(path, self.at("2026-10-11T16:00:00")))

    def test_login_before_launch_is_ignored(self):
        path = self.write(login_entries("2026-10-11T15:13:50.000Z") + [user("continue")])
        self.assertFalse(login_since(path, self.at("2026-10-11T16:00:00")))

    def test_text_merely_mentioning_the_login_output_does_not_count(self):
        marker = "<local-command-stdout>Login successful</local-command-stdout>"
        path = self.write([
            {"type": "user", "timestamp": "2026-10-11T16:00:30Z",
             "message": {"content": [{"type": "tool_result", "content": "tests contain " + marker}]}},
            {"type": "assistant", "timestamp": "2026-10-11T16:00:31Z",
             "message": {"content": [{"type": "text", "text": "I saw " + marker}]}},
        ])
        self.assertFalse(login_since(path, 0))

    def test_second_granularity_timestamps_still_count(self):
        path = self.write(login_entries("2026-10-11T16:00:30.000Z"))
        self.assertTrue(login_since(path, self.at("2026-10-11T16:00:30") + 0.7))

    def test_other_commands_and_missing_files(self):
        path = self.write([{"type": "user", "timestamp": "2026-10-11T16:00:30Z",
                            "message": {"content": "<local-command-stdout>Model set to Opus</local-command-stdout>"}}])
        self.assertFalse(login_since(path, 0))
        self.assertFalse(login_since("/nonexistent.jsonl", 0))
