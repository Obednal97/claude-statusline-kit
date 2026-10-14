import json
import os
import tempfile
import unittest
from unittest import mock

from claude_rotate import statusline
from claude_rotate.state import StateStore

PAYLOAD = {
    "model": {"display_name": "Opus"},
    "rate_limits": {
        "five_hour": {"used_percentage": 23.5, "resets_at": 1000},
        "seven_day": {"used_percentage": 41.2, "resets_at": 2000},
    },
}


class StatuslineTest(unittest.TestCase):
    def setUp(self):
        self.store = StateStore(tempfile.mkdtemp())
        self.env = {"RCLAUDE_PID": "1", "RCLAUDE_ACCOUNT": "a", "PATH": os.environ["PATH"]}

    def render(self, payload=PAYLOAD, **env):
        e = dict(self.env)
        e.update(env)
        text = payload if isinstance(payload, str) else json.dumps(payload)
        return statusline.render(text, e, self.store, 500.0)

    def test_records_usage_for_active_account(self):
        self.render()
        acct = self.store.read()["accounts"]["a"]
        self.assertEqual(acct["five_hour"], {"pct": 23.5, "resets_at": 1000.0, "seen_at": 500.0})
        self.assertEqual(acct["seven_day"]["pct"], 41.2)

    def test_keeps_other_account_fields(self):
        with self.store.locked() as st:
            st["accounts"]["a"] = {"limited_until": 9}
        self.render()
        self.assertEqual(self.store.read()["accounts"]["a"]["limited_until"], 9)

    def test_no_rate_limits_records_nothing(self):
        self.render({"model": {}})
        self.assertEqual(self.store.read()["accounts"], {})

    def test_prefixes_inner_first_line(self):
        out = self.render(RCLAUDE_INNER_STATUSLINE="printf 'line one\\nline two\\n'")
        self.assertEqual(out, "[a] line one\nline two")

    def test_inner_receives_the_same_stdin(self):
        out = self.render(RCLAUDE_INNER_STATUSLINE="cat")
        self.assertEqual(json.loads(out[len("[a] "):]), PAYLOAD)

    def test_failing_inner_falls_back(self):
        self.assertEqual(self.render(RCLAUDE_INNER_STATUSLINE="exit 3"), "[a] 5h 24% · wk 41%")

    def test_slow_inner_falls_back(self):
        with mock.patch.object(statusline, "INNER_TIMEOUT_SECONDS", 0.3):
            self.assertEqual(self.render(RCLAUDE_INNER_STATUSLINE="sleep 5; echo late"), "[a] 5h 24% · wk 41%")

    def test_no_usage_and_no_inner(self):
        self.assertEqual(self.render({}), "[a]")
        self.assertEqual(self.render("not json"), "[a]")

    def test_usage_is_not_recorded_under_a_tier_logged_into_someone_else(self):
        import json as _json
        from claude_rotate.config import parse_config
        profile = tempfile.mkdtemp()
        with open(os.path.join(profile, ".claude.json"), "w") as f:
            _json.dump({"oauthAccount": {"emailAddress": "someone@else.com"}}, f)
        config = parse_config({"tiers": [{"name": "a", "config_dir": profile, "auth": "login", "email": "a@x.com"}]})
        out = statusline.render(json.dumps(PAYLOAD), self.env, self.store, 500.0, config)
        self.assertEqual(self.store.read()["accounts"], {})
        self.assertTrue(out.startswith("[a"))

    def test_usage_is_not_recorded_after_a_login_inside_the_session(self):
        transcript = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "user", "timestamp": "2026-10-11T16:00:30.000Z",
                                "message": {"content": "<local-command-stdout>Login successful</local-command-stdout>"}}) + "\n")
        with self.store.locked() as st:
            st["launchers"]["1"] = {"account": "a", "launched_at": 0}
        payload = dict(PAYLOAD, transcript_path=transcript)
        out = statusline.render(json.dumps(payload), self.env, self.store, 500.0)
        self.assertNotIn("a", self.store.read()["accounts"])
        self.assertTrue(out.startswith("[a?]"))

    def test_outside_rclaude_prints_nothing(self):
        self.assertEqual(statusline.render(json.dumps(PAYLOAD), {"PATH": "/bin"}, self.store, 1.0), "")
