import json
import os
import subprocess
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "rclaude")
CONFIG = {"tiers": [
    {"name": "a", "config_dir": "/tmp/rclaude-test-p", "auth": "login"},
    {"name": "b", "config_dir": "/tmp/rclaude-test-p", "auth": "token"},
    {"name": "work", "config_dir": None, "auth": "login"},
]}


# Shaped like a whole `claude setup-token` token; built so the public-repo scan stays clean.
FULL_TOKEN = "sk-ant-oat01-" + "secret_VALUE" + "x" * 82  # 107, like a real one


class CliTest(unittest.TestCase):
    def run_cli(self, args, env_extra=None, stdin=""):
        env = {"PATH": os.environ["PATH"], "HOME": tempfile.mkdtemp(), "USER": os.environ.get("USER", "test"),
               "RCLAUDE_STATE_DIR": tempfile.mkdtemp()}
        env_extra = dict(env_extra or {})
        env.update(env_extra or {})
        # start_new_session: no controlling terminal, so getpass reads stdin instead of /dev/tty
        p = subprocess.run([ENTRY] + args, env=env, input=stdin, capture_output=True, text=True,
                           timeout=30, start_new_session=True)
        return p.returncode, p.stdout, p.stderr

    def config_file(self):
        path = os.path.join(tempfile.mkdtemp(), "accounts.json")
        with open(path, "w") as f:
            json.dump(CONFIG, f)
        return path

    def test_missing_config_is_a_clear_error(self):
        code, out, err = self.run_cli([], {"RCLAUDE_CONFIG": "/nonexistent/accounts.json"})
        self.assertEqual(code, 2)
        self.assertIn("config error: no config at /nonexistent/accounts.json", err)
        self.assertNotIn("Traceback", err)

    def test_status_prints_table(self):
        code, out, err = self.run_cli(["status"], {"RCLAUDE_CONFIG": self.config_file()})
        self.assertEqual(code, 0, err)
        self.assertIn("ACCOUNT", out)
        self.assertEqual(len(out.strip().splitlines()), 4)

    def test_internal_commands_outside_rclaude_are_silent(self):
        for args in (["_hook", "stop"], ["_hook", "stop-failure"], ["_hook", "session-start"], ["_statusline"]):
            self.assertEqual(self.run_cli(args, {"RCLAUDE_CONFIG": "/nonexistent"}, stdin="{}"), (0, "", ""), args)

    def test_hook_errors_never_surface(self):
        result = self.run_cli(["_hook", "stop"], {"RCLAUDE_PID": "1", "RCLAUDE_ACCOUNT": "a",
                                                  "RCLAUDE_CONFIG": "/nonexistent"}, stdin="{}")
        self.assertEqual(result, (0, "", ""))

    def test_add_token_stores_without_echoing(self):
        kc = os.path.join(tempfile.mkdtemp(), "kc.json")
        code, out, err = self.run_cli(["add-token", "b"], {"RCLAUDE_TOKEN_FILE": kc,
                                                           "RCLAUDE_CONFIG": self.config_file()},
                                      stdin=FULL_TOKEN + "\n")
        self.assertEqual(code, 0, err)
        self.assertIn(kc + " (as b)", out)
        self.assertNotIn("secret_VALUE", out + err)
        with open(kc) as f:
            self.assertEqual(json.load(f), {"b": FULL_TOKEN})

    def add_token_with_block(self, last_error, stdin=FULL_TOKEN + "\n"):
        """Run add-token b with b blocked for a day after `last_error`; returns (code, out, b's state after)."""
        state = tempfile.mkdtemp()
        until = time.time() + 86400
        with open(os.path.join(state, "state.json"), "w") as f:
            json.dump({"accounts": {"b": {"limited_until": until, "last_error": last_error}},
                       "launchers": {}, "sessions": {}}, f)
        code, out, err = self.run_cli(["add-token", "b"], {"RCLAUDE_TOKEN_FILE": os.path.join(state, "kc.json"),
                                                           "RCLAUDE_CONFIG": self.config_file(),
                                                           "RCLAUDE_STATE_DIR": state}, stdin=stdin)
        with open(os.path.join(state, "state.json")) as f:
            return code, out, json.load(f)["accounts"]["b"], until

    def test_add_token_lifts_a_login_error_block(self):
        code, out, acct, _ = self.add_token_with_block("Please run /login · API Error: 401 OAuth access token is invalid.")
        self.assertEqual(code, 0)
        self.assertIn("Lifted the block on b", out)
        self.assertNotIn("limited_until", acct)

    def test_add_token_keeps_a_plan_limit(self):
        code, out, acct, until = self.add_token_with_block("You've hit your session limit · resets 3pm")
        self.assertEqual(code, 0)
        self.assertNotIn("Lifted", out)
        self.assertEqual(acct["limited_until"], until)

    def test_refused_token_keeps_the_block(self):
        code, out, acct, until = self.add_token_with_block("Please run /login · API Error: 401",
                                                           stdin=FULL_TOKEN[:80] + "\n")
        self.assertEqual(code, 1)
        self.assertEqual(acct["limited_until"], until)

    def test_add_token_refuses_the_first_line_of_a_wrapped_token(self):
        # getpass reads one line, so a token wrapped across two lines arrives cut at the wrap.
        kc = os.path.join(tempfile.mkdtemp(), "kc.json")
        code, out, err = self.run_cli(["add-token", "b"], {"RCLAUDE_TOKEN_FILE": kc,
                                                           "RCLAUDE_CONFIG": self.config_file()},
                                      stdin=FULL_TOKEN[:80] + "\n" + FULL_TOKEN[80:] + "\n")
        self.assertEqual(code, 1)
        self.assertIn("cut short", err)
        self.assertIn("nothing was saved", err)
        self.assertIn("rclaude add-token b again", err)
        self.assertNotIn("Saved", out)
        self.assertNotIn("secret_VALUE", out + err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(kc))

    def test_failed_kill_drops_the_switch_request(self):
        state = tempfile.mkdtemp()
        path = os.path.join(state, "switch-999998.json")
        with open(path, "w") as f:
            json.dump({"reason": "rebalance"}, f)
        result = self.run_cli(["_kill", "999998", "999997"], {"RCLAUDE_STATE_DIR": state, "RCLAUDE_KILL_DELAY": "0"})
        self.assertEqual(result[0], 0)
        self.assertFalse(os.path.exists(path))

    def test_add_token_rejects_bad_name(self):
        code, out, err = self.run_cli(["add-token", "bad name"])
        self.assertEqual(code, 2)
        self.assertIn("usage", err)
