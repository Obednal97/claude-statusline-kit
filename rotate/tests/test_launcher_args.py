import json
import os
import tempfile
import unittest
from unittest import mock

from claude_rotate import launcher
from claude_rotate.config import Tier
from claude_rotate.state import StateStore

P = Tier("a", "/home/x/.claude-personal", "login")
T = Tier("b", "/home/x/.claude-personal", "token")
W = Tier("work", None, "login")


class PassthroughTest(unittest.TestCase):
    def test_subcommands_and_flags(self):
        for args in (["auth", "status"], ["--dangerously-skip-permissions", "mcp", "list"],
                     ["-p", "hi"], ["--version"], ["--help"], ["agents"], ["plugins", "list"]):
            self.assertTrue(launcher.is_passthrough(args), args)

    def test_config_and_migrate_installer_are_prompts_since_2_1_299(self):
        # Claude Code 2.1.299 dropped both subcommands, so `rclaude config` starts a rotating session.
        self.assertFalse(launcher.is_passthrough(["config"]))
        self.assertFalse(launcher.is_passthrough(["migrate-installer"]))
        self.assertTrue(launcher.is_passthrough(["mcp", "list"]))

    def test_interactive(self):
        for args in ([], ["--dangerously-skip-permissions"], ["fix the auth bug"], ["--model", "auth"]):
            self.assertFalse(launcher.is_passthrough(args), args)


class StripSessionArgsTest(unittest.TestCase):
    def test_keeps_flags_drops_prompt(self):
        # Review focus: the user's own prompt must not be sent again on relaunch.
        self.assertEqual(
            launcher.strip_session_args(["--dangerously-skip-permissions", "--model", "opus", "fix the bug"]),
            ["--dangerously-skip-permissions", "--model", "opus"])

    def test_value_option_at_the_end_is_kept(self):
        self.assertEqual(launcher.strip_session_args(["--model", "opus"]), ["--model", "opus"])

    def test_prompt_anywhere_is_dropped(self):
        self.assertEqual(launcher.strip_session_args(["fix the bug", "--model", "opus"]), ["--model", "opus"])

    def test_real_value_options_keep_their_values(self):
        args = ["--effort", "high", "-n", "work", "--plugin-dir", "./p"]  # value option last
        self.assertEqual(launcher.strip_session_args(args), args)

    def test_variadic_options_keep_all_values(self):
        self.assertEqual(launcher.strip_session_args(["--verbose", "--add-dir", "../a", "../b"]),
                         ["--verbose", "--add-dir", "../a", "../b"])

    def test_optional_value_options(self):
        self.assertEqual(launcher.strip_session_args(["--from-pr", "--dangerously-skip-permissions"]),
                         ["--dangerously-skip-permissions"])
        self.assertEqual(launcher.strip_session_args(["-d", "api", "--verbose"]), ["-d", "api", "--verbose"])
        self.assertEqual(launcher.strip_session_args(["-w", "feat", "--verbose"]), ["--verbose"])
        self.assertEqual(launcher.strip_session_args(["--teleport", "abc"]), [])

    def test_drops_session_selection(self):
        self.assertEqual(launcher.strip_session_args(
            ["-c", "--resume", "abc", "--session-id", "x", "--fork-session", "--from-pr", "12", "--verbose"]),
            ["--verbose"])
        self.assertEqual(launcher.strip_session_args(["-r", "--verbose"]), ["--verbose"])
        self.assertEqual(launcher.strip_session_args(["--resume=abc", "--verbose"]), ["--verbose"])


class BuildEnvTest(unittest.TestCase):
    BASE = {"PATH": "/bin", "CLAUDE_CONFIG_DIR": "/stray", "CLAUDE_CODE_OAUTH_TOKEN": "stray",
            "RCLAUDE_ACCOUNT": "old", "RCLAUDE_STATE_DIR": "/s"}

    def test_login_tier_drops_stray_values(self):
        # Review focus: a stray config dir or token in the calling shell must not leak in.
        env = launcher.build_env(self.BASE, W, None)
        for key in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "RCLAUDE_ACCOUNT"):
            self.assertNotIn(key, env)
        self.assertEqual((env["PATH"], env["RCLAUDE_STATE_DIR"]), ("/bin", "/s"))

    def test_token_tier(self):
        env = launcher.build_env(self.BASE, T, "tok", launcher_pid=77, inner_statusline="ccstatusline")
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], "/home/x/.claude-personal")
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], "tok")
        self.assertEqual((env["RCLAUDE_ACCOUNT"], env["RCLAUDE_PID"], env["RCLAUDE_INNER_STATUSLINE"]),
                         ("b", "77", "ccstatusline"))

    def test_exports_auth_and_email_for_widgets(self):
        tier = Tier("spare", "/home/x/.claude-personal", "token", "you.too@example.com")
        env = launcher.build_env(self.BASE, tier, "tok", launcher_pid=77)
        self.assertEqual((env["RCLAUDE_AUTH"], env["RCLAUDE_ACCOUNT_EMAIL"]), ("token", "you.too@example.com"))
        env = launcher.build_env(self.BASE, P, None, launcher_pid=77)
        self.assertEqual((env["RCLAUDE_AUTH"], env["RCLAUDE_ACCOUNT_EMAIL"]), ("login", ""))

    def test_stale_widget_values_are_dropped(self):
        base = dict(self.BASE, RCLAUDE_AUTH="token", RCLAUDE_ACCOUNT_EMAIL="old@example.com")
        env = launcher.build_env(base, W, None)
        self.assertNotIn("RCLAUDE_AUTH", env)
        self.assertNotIn("RCLAUDE_ACCOUNT_EMAIL", env)

    def test_does_not_mutate_base(self):
        base = dict(self.BASE)
        launcher.build_env(base, P, None)
        self.assertEqual(base, self.BASE)


class InnerStatuslineTest(unittest.TestCase):
    def tier_with(self, settings):
        d = tempfile.mkdtemp()
        if settings is not None:
            with open(os.path.join(d, "settings.json"), "w") as f:
                json.dump(settings, f)
        return Tier("x", d, "login")

    def test_reads_profile_command(self):
        tier = self.tier_with({"statusLine": {"type": "command", "command": "ccstatusline"}})
        self.assertEqual(launcher.inner_statusline_for(tier), "ccstatusline")

    def test_missing_or_not_a_command(self):
        self.assertIsNone(launcher.inner_statusline_for(self.tier_with(None)))
        self.assertIsNone(launcher.inner_statusline_for(self.tier_with({"statusLine": {"type": "static"}})))

    def test_never_wraps_itself(self):
        tier = self.tier_with({"statusLine": {"type": "command", "command": "/x/rclaude _statusline"}})
        self.assertIsNone(launcher.inner_statusline_for(tier))

    def test_default_profile_reads_home_dot_claude(self):
        home = tempfile.mkdtemp()
        os.makedirs(os.path.join(home, ".claude"))
        with open(os.path.join(home, ".claude", "settings.json"), "w") as f:
            json.dump({"statusLine": {"type": "command", "command": "work-line"}}, f)
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(launcher.inner_statusline_for(W), "work-line")


class GeneratedSettingsTest(unittest.TestCase):
    def test_writes_hooks_and_statusline(self):
        store = StateStore(tempfile.mkdtemp())
        path = launcher.write_generated_settings(store, "/opt/my tools/rclaude")
        self.assertEqual(path, store.settings_path)
        with open(path) as f:
            s = json.load(f)
        self.assertEqual(s["statusLine"], {"type": "command", "command": "'/opt/my tools/rclaude' _statusline"})
        self.assertEqual(s["hooks"]["Stop"][0]["hooks"][0]["command"], "'/opt/my tools/rclaude' _hook stop")
        self.assertEqual(s["hooks"]["StopFailure"][0]["hooks"][0]["command"],
                         "'/opt/my tools/rclaude' _hook stop-failure")
        self.assertEqual(s["hooks"]["SessionStart"][0]["hooks"][0]["command"],
                         "'/opt/my tools/rclaude' _hook session-start")


class PlanRelaunchTest(unittest.TestCase):
    LIMIT = {"reason": "limit", "session_id": "s1", "transcript_path": "/t/s1.jsonl"}
    REBALANCE = {"reason": "rebalance", "session_id": "s1", "transcript_path": "/t/s1.jsonl"}

    def test_same_profile_limit_resumes_with_prompt(self):
        self.assertEqual(launcher.plan_relaunch(self.LIMIT, P, T, None),
                         (["--resume", "s1", launcher.LIMIT_PROMPT], None))

    def test_limit_relaunch_on_the_same_account_sends_no_prompt(self):
        # Code review: the hook's target had no stored token, so the launcher fell back to the limited
        # account itself. "Now running on a different account" would be false and would hit the limit again.
        self.assertEqual(launcher.plan_relaunch(self.LIMIT, P, P, None), (["--resume", "s1"], None))

    def test_same_profile_rebalance_resumes_quietly(self):
        self.assertEqual(launcher.plan_relaunch(self.REBALANCE, T, P, None), (["--resume", "s1"], None))

    def test_limit_found_on_resume_resumes_quietly_on_the_next_account(self):
        request = {"reason": "limit-on-resume", "session_id": "s1", "transcript_path": "/t/s1.jsonl"}
        self.assertEqual(launcher.plan_relaunch(request, P, T, None), (["--resume", "s1"], None))

    def test_into_other_profile_hands_off_and_remembers(self):
        args, return_session = launcher.plan_relaunch(self.LIMIT, T, W, None)
        self.assertEqual(args, [launcher.handoff_prompt("/t/s1.jsonl")])
        self.assertEqual(return_session, {"session_id": "s1", "config_dir": T.config_dir})

    def test_back_to_remembered_profile_resumes_it(self):
        work_request = {"reason": "rebalance", "session_id": "w1", "transcript_path": "/t/w1.jsonl"}
        remembered = {"session_id": "s1", "config_dir": P.config_dir}
        self.assertEqual(launcher.plan_relaunch(work_request, W, P, remembered),
                         (["--resume", "s1", launcher.handoff_prompt("/t/w1.jsonl")], None))

    def test_same_profile_keeps_return_session(self):
        remembered = {"session_id": "w1", "config_dir": None}
        self.assertEqual(launcher.plan_relaunch(self.LIMIT, P, T, remembered)[1], remembered)

    def test_missing_session_id(self):
        self.assertEqual(launcher.plan_relaunch({"reason": "limit"}, P, T, None), ([], None))
        self.assertEqual(launcher.plan_relaunch({"reason": "limit"}, P, W, None), ([], None))
