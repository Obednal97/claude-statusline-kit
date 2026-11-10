import json
import os
import tempfile
import unittest

from claude_rotate import models
from claude_rotate.config import parse_config

NOW = 1_800_000_000.0
CONFIG = parse_config({"tiers": [
    {"name": "main", "config_dir": "/tmp/p", "auth": "login", "email": "you@example.com"},
    {"name": "work", "config_dir": "/tmp/p", "auth": "token"},
]})
LISTS = {"tok-main": ["claude-fable-5-2", "claude-opus-5-5"], "tok-work": ["claude-mythos-5-2", "claude-fable-5-2"]}


class ReadTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.calls = []

    def get(self, token):
        self.calls.append(token)
        if token not in LISTS:
            raise OSError("401")
        return LISTS[token]

    def read(self, now=NOW, tok=lambda t, now: "tok-" + t.name, **kw):
        return models.read(CONFIG, self.dir, now, token=tok, get=self.get, **kw)

    def test_reads_each_account_with_its_own_login(self):
        got = self.read()
        self.assertEqual({k: v["models"] for k, v in got.items()}, {"main": LISTS["tok-main"], "work": LISTS["tok-work"]})

    def test_cached_until_stale(self):
        self.read()
        self.read(now=NOW + 60)
        self.assertEqual(len(self.calls), 2)
        self.read(now=NOW + models.MAX_AGE + 1)
        self.assertEqual(len(self.calls), 4)

    def test_keeps_the_last_list_when_a_login_cant_be_read(self):
        self.read()
        later = self.read(now=NOW + models.MAX_AGE + 1, tok=lambda t, now: None if t.name == "main" else "bad")
        self.assertEqual(later["main"]["models"], LISTS["tok-main"])
        self.assertEqual(later["work"]["models"], LISTS["tok-work"])

    def test_unknown_account_left_out(self):
        self.assertEqual(set(self.read(tok=lambda t, now: "tok-work" if t.name == "work" else None)), {"work"})

    def test_drops_accounts_no_longer_configured(self):
        with open(os.path.join(self.dir, "models.json"), "w") as f:
            json.dump({"gone": {"models": ["x"], "t": NOW}}, f)
        self.assertNotIn("gone", self.read())


class LoginTokenTest(unittest.TestCase):
    def test_expired_login_is_not_used(self):
        tier = parse_config({"tiers": [{"name": "m", "config_dir": tempfile.mkdtemp(), "auth": "login"}]}).tiers[0]
        creds = {"claudeAiOauth": {"accessToken": "a", "expiresAt": (NOW - 10) * 1000}}
        if models.sys.platform == "darwin":
            self.skipTest("reads the Keychain on macOS")
        with open(os.path.join(tier.config_dir, ".credentials.json"), "w") as f:
            json.dump(creds, f)
        self.assertIsNone(models.login_token(tier, NOW))
        creds["claudeAiOauth"]["expiresAt"] = (NOW + 3600) * 1000
        with open(os.path.join(tier.config_dir, ".credentials.json"), "w") as f:
            json.dump(creds, f)
        self.assertEqual(models.login_token(tier, NOW), "a")


if __name__ == "__main__":
    unittest.main()
