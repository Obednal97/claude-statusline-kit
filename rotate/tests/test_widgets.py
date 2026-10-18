import json
import os
import shutil
import subprocess
import tempfile
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WIDGETS = os.path.join(KIT, "statusline")
RATE = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 62, "resets_at": 4102444800},
                                   "seven_day": {"used_percentage": 41, "resets_at": 4102444800}}})


@unittest.skipUnless(shutil.which("node") and shutil.which("bash"), "needs node and bash")
class WidgetTest(unittest.TestCase):
    def run_widget(self, name, env_extra, org_type="claude_max", stdin="{}"):
        profile = tempfile.mkdtemp()
        with open(os.path.join(profile, ".claude.json"), "w") as f:
            json.dump({"oauthAccount": {"emailAddress": "you@example.com", "organizationType": org_type}}, f)
        env = {"PATH": os.environ["PATH"], "HOME": tempfile.mkdtemp(), "CLAUDE_CONFIG_DIR": profile}
        env.update(env_extra)
        p = subprocess.run(["bash", os.path.join(WIDGETS, name)], env=env, input=stdin,
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_account_shows_the_profile_login_normally(self):
        self.assertEqual(self.run_widget("account.sh", {}), "👤 you@example.com")
        login_tier = {"RCLAUDE_AUTH": "login", "RCLAUDE_ACCOUNT": "main", "RCLAUDE_ACCOUNT_EMAIL": "x@example.com"}
        self.assertEqual(self.run_widget("account.sh", login_tier), "👤 you@example.com")

    def test_account_shows_the_token_tier_email(self):
        out = self.run_widget("account.sh", {"RCLAUDE_AUTH": "token", "RCLAUDE_ACCOUNT": "spare",
                                             "RCLAUDE_ACCOUNT_EMAIL": "you.too@example.com"})
        self.assertEqual(out, "👤 you.too@example.com")

    def test_account_token_tier_without_email_shows_name(self):
        # Review focus: a token tier with no "email" in accounts.json still gets a sensible label.
        out = self.run_widget("account.sh", {"RCLAUDE_AUTH": "token", "RCLAUDE_ACCOUNT": "spare",
                                             "RCLAUDE_ACCOUNT_EMAIL": ""})
        self.assertEqual(out, "👤 spare")

    def test_plan_usage_hidden_on_a_team_login(self):
        self.assertEqual(self.run_widget("plan-usage.sh", {}, org_type="claude_team", stdin=RATE), "")

    def test_plan_usage_shown_on_a_token_tier(self):
        out = self.run_widget("plan-usage.sh", {"RCLAUDE_AUTH": "token"}, org_type="claude_team", stdin=RATE)
        self.assertIn("5h: 62%", out)
        self.assertIn("7d: 41%", out)

    def test_plan_usage_colour_fades_from_green_to_red(self):
        def rgb(pct, env):
            stdin = json.dumps({"rate_limits": {"five_hour": {"used_percentage": pct}}})
            return self.run_widget("plan-usage.sh", env, stdin=stdin).split("m", 1)[0]
        truecolor = {"COLORTERM": "truecolor"}
        self.assertEqual(rgb(0, truecolor), "\x1b[38;2;63;185;80")
        self.assertEqual(rgb(50, truecolor), "\x1b[38;2;210;153;34")
        self.assertEqual(rgb(100, truecolor), "\x1b[38;2;248;81;73")
        self.assertEqual(rgb(25, truecolor), "\x1b[38;2;137;169;57")
        self.assertEqual(rgb(140, truecolor), rgb(100, truecolor))
        # Without COLORTERM it falls back to the 256-colour palette.
        self.assertEqual(rgb(0, {}), "\x1b[38;5;71")
        self.assertEqual(rgb(100, {}), "\x1b[38;5;203")
