import json
import os
import tempfile
import unittest
from unittest import mock

from claude_rotate.config import EXAMPLE, ConfigError, Tier, load_config, parse_config


def example():
    return {
        "tiers": [
            {"name": "main", "config_dir": "~/.claude-personal", "auth": "login"},
            {"name": "spare", "config_dir": "~/.claude-personal", "auth": "token"},
            {"name": "work", "config_dir": None, "auth": "login"},
        ],
        "min_upswitch_interval_seconds": 120,
    }


class ParseConfigTest(unittest.TestCase):
    def test_valid_example(self):
        cfg = parse_config(example())
        personal = os.path.expanduser("~/.claude-personal")
        self.assertEqual(cfg.tiers, (
            Tier("main", personal, "login"),
            Tier("spare", personal, "token"),
            Tier("work", None, "login"),
        ))
        self.assertEqual(cfg.min_upswitch_interval_seconds, 120)
        self.assertEqual(cfg.tier("work").auth, "login")
        self.assertIsNone(cfg.tier("nope"))

    def test_example_file_matches_built_in_example(self):
        rotate_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(rotate_dir, "accounts.example.json")) as f:
            from_file = json.load(f)
        self.assertEqual(from_file, json.loads(EXAMPLE))
        cfg = parse_config(from_file)
        self.assertEqual([(t.name, t.auth) for t in cfg.tiers], [("main", "login"), ("spare", "token")])
        self.assertEqual({t.config_dir for t in cfg.tiers}, {os.path.expanduser("~/.claude-personal")})

    def test_optional_email_per_tier(self):
        d = example()
        d["tiers"][0]["email"] = "you@example.com"
        cfg = parse_config(d)
        self.assertEqual(cfg.tiers[0].email, "you@example.com")
        self.assertIsNone(cfg.tiers[1].email)

    def test_rejects_bad_email(self):
        for bad in ("not-an-email", 7, ""):
            d = example()
            d["tiers"][0]["email"] = bad
            self.assertInvalid(d, "email")

    def test_interval_defaults_to_120(self):
        data = example()
        del data["min_upswitch_interval_seconds"]
        self.assertEqual(parse_config(data).min_upswitch_interval_seconds, 120)

    def assertInvalid(self, data, fragment):
        with self.assertRaises(ConfigError) as ctx:
            parse_config(data)
        self.assertIn(fragment, str(ctx.exception))

    def test_rejects_bad_shapes(self):
        self.assertInvalid([], "top level")
        self.assertInvalid({"tiers": []}, "non-empty")
        d = example(); d["tiers"][0]["auth"] = "password"; self.assertInvalid(d, "auth")
        d = example(); d["tiers"][1]["name"] = "main"; self.assertInvalid(d, "duplicated")
        d = example(); d["tiers"][0]["name"] = 'bad"name'; self.assertInvalid(d, "name")
        d = example(); d["tiers"][0]["name"] = "trailing\n"; self.assertInvalid(d, "name")
        d = example(); d["min_upswitch_interval_seconds"] = -1; self.assertInvalid(d, "min_upswitch")
        d = example(); d["min_upswitch_interval_seconds"] = True; self.assertInvalid(d, "min_upswitch")
        d = example(); d["tiers"][0]["config_dir"] = ""; self.assertInvalid(d, "config_dir")

    def test_rejects_explicit_default_profile_dir(self):
        # Review focus: an explicit ~/.claude makes Claude Code read a different, empty Keychain entry.
        d = example()
        d["tiers"][2]["config_dir"] = "~/.claude"
        self.assertInvalid(d, "use null")


class LoadConfigTest(unittest.TestCase):
    def test_missing_file_explains_how_to_create_it(self):
        with self.assertRaises(ConfigError) as ctx:
            load_config("/nonexistent/accounts.json")
        self.assertIn("no config at /nonexistent/accounts.json", str(ctx.exception))
        self.assertIn('"tiers"', str(ctx.exception))

    def test_invalid_json(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not json")
        self.addCleanup(os.unlink, f.name)
        with self.assertRaises(ConfigError) as ctx:
            load_config(f.name)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_reads_path_from_env(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(example(), f)
        self.addCleanup(os.unlink, f.name)
        with mock.patch.dict(os.environ, {"RCLAUDE_CONFIG": f.name}):
            self.assertEqual(len(load_config().tiers), 3)
