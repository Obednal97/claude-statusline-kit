import unittest

from claude_rotate.config import parse_config
from claude_rotate.status import format_status

NOW = 1_800_000_000.0
CONFIG = parse_config({"tiers": [
    {"name": "a", "config_dir": "/p", "auth": "login"},
    {"name": "b", "config_dir": "/p", "auth": "token"},
    {"name": "work", "config_dir": None, "auth": "login"},
]})


class StatusTest(unittest.TestCase):
    def test_table(self):
        state = {
            "accounts": {
                "a": {
                    "five_hour": {"pct": 100, "resets_at": NOW + 3600, "seen_at": NOW - 120},
                    "seven_day": {"pct": 40.4, "resets_at": NOW + 86400, "seen_at": NOW - 120},
                },
                "b": {"five_hour": {"pct": 12, "resets_at": NOW + 100, "seen_at": NOW - 30}},
            },
            "launchers": {"1": {"account": "b"}, "2": {"account": "b"}},
        }
        logins = {"a": "you@example.com", "b": "token", "work": "you@work.example"}
        lines = format_status(CONFIG, state, NOW, logins).splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("#"))
        a, b, w = lines[1].split(), lines[2].split(), lines[3].split()
        self.assertEqual(a[:5], ["1", "a", "limited", "100%", "40%"])
        self.assertIn("(in 1h00m)", lines[1])
        self.assertIn("2m ago", lines[1])
        self.assertEqual(b[:5], ["2", "b", "available", "12%", "-"])
        self.assertEqual(b[-2:], ["2", "token"])
        self.assertTrue(lines[0].rstrip().endswith("LOGIN"))
        self.assertEqual(a[-1], "you@example.com")
        self.assertEqual(w[:5], ["3", "work", "available", "-", "-"])
        self.assertIn("never", lines[3])
        self.assertEqual(w[-2:], ["0", "you@work.example"])
