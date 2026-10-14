import json
import os
import signal
import subprocess
import tempfile
import time
import unittest

from claude_rotate.launcher import LIMIT_PROMPT, handoff_prompt

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY = os.path.join(ROOT, "rclaude")
FAKE = os.path.join(ROOT, "tests", "fake_claude.py")
LIMIT_MSG = "You've hit your session limit · resets 3:45pm"


def failure(session, message=LIMIT_MSG, error="rate_limit"):
    return {"hook": "StopFailure", "payload": {
        "session_id": session, "transcript_path": "/t/%s.jsonl" % session, "cwd": "/w",
        "hook_event_name": "StopFailure", "error": error, "error_details": "429",
        "last_assistant_message": message}}


def stop(session, transcript=None):
    return {"hook": "Stop", "payload": {
        "session_id": session, "transcript_path": transcript or "/t/%s.jsonl" % session, "cwd": "/w",
        "hook_event_name": "Stop"}}


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


AFTER_STOP_HOOKS = [{"type": "system", "subtype": "stop_hook_summary"}, {"type": "system", "subtype": "turn_duration"}]


class Harness:
    def __init__(self, tokens=None, state=None, min_interval=0, emails=None, logged_in=None):
        self.tmp = tempfile.mkdtemp()
        self.personal = os.path.join(self.tmp, "personal")
        self.home = os.path.join(self.tmp, "home")
        self.state_dir = os.path.join(self.tmp, "state")
        for d in (self.personal, os.path.join(self.home, ".claude"), self.state_dir):
            os.makedirs(d)
        self._json(os.path.join(self.personal, "settings.json"),
                   {"statusLine": {"type": "command", "command": "echo personal-line"}})
        self._json(os.path.join(self.home, ".claude", "settings.json"),
                   {"statusLine": {"type": "command", "command": "echo work-line"}})
        self.config = os.path.join(self.tmp, "accounts.json")
        tiers = [
            {"name": "a", "config_dir": self.personal, "auth": "login"},
            {"name": "b", "config_dir": self.personal, "auth": "token"},
            {"name": "work", "config_dir": None, "auth": "login"},
        ]
        for tier in tiers:
            if emails and tier["name"] in emails:
                tier["email"] = emails[tier["name"]]
        self._json(self.config, {"tiers": tiers, "min_upswitch_interval_seconds": min_interval})
        if logged_in:  # (email, org) the personal profile is logged into
            self._json(os.path.join(self.personal, ".claude.json"),
                       {"oauthAccount": {"emailAddress": logged_in[0], "organizationUuid": logged_in[1]}})
        self.token_file = os.path.join(self.tmp, "tokens.json")
        self._json(self.token_file, {"b": "tok-b"} if tokens is None else tokens)
        if state:
            self._json(os.path.join(self.state_dir, "state.json"), state)
        self.log = os.path.join(self.tmp, "fake.log")
        self.script = os.path.join(self.tmp, "script.json")
        self.ready = os.path.join(self.tmp, "ready")

    @staticmethod
    def _json(path, data):
        with open(path, "w") as f:
            json.dump(data, f)

    def env(self):
        return {
            "PATH": os.environ["PATH"], "HOME": self.home, "USER": os.environ.get("USER", "test"),
            "RCLAUDE_CONFIG": self.config, "RCLAUDE_STATE_DIR": self.state_dir,
            "RCLAUDE_TOKEN_FILE": self.token_file, "RCLAUDE_CLAUDE_BIN": FAKE, "RCLAUDE_KILL_DELAY": "0.1",
            "FAKE_CLAUDE_LOG": self.log, "FAKE_CLAUDE_SCRIPT": self.script,
            # Review focus: stray values from the calling shell must not leak into any tier.
            "CLAUDE_CONFIG_DIR": "/stray/config", "CLAUDE_CODE_OAUTH_TOKEN": "stray-token",
        }

    def start(self, args, script):
        self._json(self.script, script)
        return subprocess.Popen([ENTRY] + list(args), env=self.env(), cwd=self.tmp,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def run(self, args, script, timeout=30):
        proc = self.start(args, script)
        _, err = proc.communicate(timeout=timeout)
        return proc.returncode, err, self.invocations()

    def invocations(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def state(self):
        with open(os.path.join(self.state_dir, "state.json")) as f:
            return json.load(f)


def accounts(inv):
    return [i["env"]["RCLAUDE_ACCOUNT"] for i in inv]


def exit_codes(inv):
    return [i["code"] for i in inv]  # 143 = stopped by rclaude's SIGTERM; 99 would mean the kill never came


class IntegrationTest(unittest.TestCase):
    def test_limit_moves_to_next_personal_account_and_resumes(self):
        h = Harness()
        code, err, inv = h.run(["--dangerously-skip-permissions"], [
            [{"statusline": {"rate_limits": {"five_hour": {"used_percentage": 100, "resets_at": time.time() + 900}}}},
             failure("s1"), {"wait_for_term": True}],
            [{"exit": 0}],
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["a", "b"])
        self.assertEqual(exit_codes(inv), [143, 0])
        first, second = inv
        settings = os.path.join(h.state_dir, "rclaude-settings.json")
        self.assertEqual(first["argv"], ["--settings", settings, "--dangerously-skip-permissions"])
        self.assertEqual(first["env"]["CLAUDE_CONFIG_DIR"], h.personal)
        self.assertIsNone(first["env"]["CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertEqual(first["outputs"], ["[a] personal-line\n"])
        # session args and the prompt go before the user's options, so no option can swallow them
        self.assertEqual(second["argv"],
                         ["--settings", settings, "--resume", "s1", LIMIT_PROMPT, "--dangerously-skip-permissions"])
        self.assertEqual(second["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "tok-b")
        self.assertEqual((first["env"]["RCLAUDE_AUTH"], second["env"]["RCLAUDE_AUTH"]), ("login", "token"))
        self.assertGreater(h.state()["accounts"]["a"]["limited_until"], time.time())
        self.assertEqual(h.state()["launchers"], {})

    def test_missing_token_on_the_target_resumes_quietly_on_the_same_account(self):
        # Code review: the hook picks b by state alone, but b's token was never stored and work is limited,
        # so the launcher can only go back to a. It must not claim "now running on a different account".
        h = Harness(tokens={}, state={"accounts": {"work": {"limited_until": time.time() + 7200}}, "launchers": {}})
        code, err, inv = h.run(["--dangerously-skip-permissions"],
                               [[failure("s1"), {"wait_for_term": True}], [{"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["a", "a"])
        self.assertEqual(exit_codes(inv), [143, 0])
        self.assertEqual(inv[1]["argv"][2:], ["--resume", "s1", "--dangerously-skip-permissions"])
        self.assertIn("rclaude add-token b", err)

    def test_falls_to_work_with_handoff_when_personal_accounts_are_limited(self):
        h = Harness(state={"accounts": {"a": {"limited_until": time.time() + 3600}}, "launchers": {}})
        code, err, inv = h.run(["--add-dir", "../lib"], [[failure("s1"), {"wait_for_term": True}], [{"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["b", "work"])
        self.assertEqual(exit_codes(inv), [143, 0])
        work = inv[1]
        self.assertIsNone(work["env"]["CLAUDE_CONFIG_DIR"])
        self.assertIsNone(work["env"]["CLAUDE_CODE_OAUTH_TOKEN"])
        self.assertEqual(work["argv"][2:], [handoff_prompt("/t/s1.jsonl"), "--add-dir", "../lib"])

    def test_moves_back_up_at_idle_after_reset(self):
        h = Harness(state={"accounts": {"a": {"limited_until": time.time() + 3600}}, "launchers": {}})
        code, err, inv = h.run([], [[{"clear_limits": ["a"]}, stop("s2"), {"wait_for_term": True}], [{"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["b", "a"])
        self.assertEqual(exit_codes(inv), [143, 0])
        self.assertEqual(inv[1]["argv"][2:], ["--resume", "s2"])

    def test_returns_from_work_to_the_original_personal_session(self):
        h = Harness(state={"accounts": {"a": {"limited_until": time.time() + 3600}}, "launchers": {}})
        code, err, inv = h.run([], [
            [failure("s1"), {"wait_for_term": True}],
            [{"clear_limits": ["a", "b"]}, stop("w1"), {"wait_for_term": True}],
            [{"exit": 0}],
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["b", "work", "a"])
        self.assertEqual(exit_codes(inv), [143, 143, 0])
        self.assertEqual(inv[2]["argv"][2:], ["--resume", "s1", handoff_prompt("/t/w1.jsonl")])

    def test_normal_exit_passes_the_exit_code_through(self):
        h = Harness()
        code, err, inv = h.run(["hello"], [[{"exit": 3}]])
        self.assertEqual(code, 3, err)
        self.assertEqual(len(inv), 1)
        self.assertEqual(inv[0]["argv"][2:], ["hello"])

    def test_missing_token_skips_that_tier_with_a_fix(self):
        h = Harness(tokens={}, state={"accounts": {"a": {"limited_until": time.time() + 3600}}, "launchers": {}})
        code, err, inv = h.run([], [[{"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["work"])
        self.assertIn("rclaude add-token b", err)

    def test_all_limited_starts_on_soonest_and_does_not_switch(self):
        now = time.time()
        h = Harness(state={"accounts": {
            "a": {"limited_until": now + 900}, "b": {"limited_until": now + 600},
            "work": {"limited_until": now + 300}}, "launchers": {}})
        code, err, inv = h.run([], [[failure("s1"), {"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["work"])
        self.assertIn("every account is limited", err)
        self.assertFalse(any(n.startswith("switch-") for n in os.listdir(h.state_dir)))

    def test_server_throttle_does_not_switch(self):
        h = Harness()
        code, err, inv = h.run([], [[failure("s1", message="Server is temporarily limiting requests"), {"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(len(inv), 1)
        self.assertNotIn("a", h.state()["accounts"])

    def test_quitting_during_a_pending_switch_up_exits_instead_of_relaunching(self):
        h = Harness(state={"accounts": {"a": {"limited_until": time.time() + 3600}}, "launchers": {}})
        code, err, inv = h.run([], [[{"clear_limits": ["a"]}, stop("s2"), {"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["b"])

    def test_config_subcommands_use_the_first_tier_even_when_it_is_limited(self):
        later = time.time() + 3600
        h = Harness(state={"accounts": {"a": {"limited_until": later}, "b": {"limited_until": later}}, "launchers": {}})
        code, err, inv = h.run(["mcp", "list"], [[{"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(inv[0]["env"]["CLAUDE_CONFIG_DIR"], h.personal)
        self.assertIsNone(inv[0]["env"]["CLAUDE_CODE_OAUTH_TOKEN"])

    def test_print_mode_uses_the_best_available_tier(self):
        h = Harness(state={"accounts": {"a": {"limited_until": time.time() + 3600}}, "launchers": {}})
        code, err, inv = h.run(["-p", "hi"], [[{"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(inv[0]["env"]["CLAUDE_CODE_OAUTH_TOKEN"], "tok-b")

    def test_tier_logged_into_the_wrong_account_is_skipped_with_a_fix(self):
        h = Harness(emails={"a": "a@x.com"}, logged_in=("someone@else.com", "org-z"))
        code, err, inv = h.run([], [[{"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["b"])
        self.assertIn("/login as a@x.com", err)

    def test_resuming_into_this_accounts_limit_switches_quietly(self):
        # Claude Code would open its limit menu before anything is sent; rclaude switches at SessionStart.
        h = Harness(emails={"a": "a@x.com"}, logged_in=("a@x.com", "org-a"))
        transcript = os.path.join(h.tmp, "s9.jsonl")
        reset = time.time() + 4 * 86400
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "hi"}}) + "\n")
            f.write(json.dumps({"type": "attachment", "attachment": {"type": "credential_org", "organizationUuid": "org-a"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "isApiErrorMessage": True, "apiError": "usage_limit_reached",
                                "error": "rate_limit", "quotaLimits": {"resetsAt": reset},
                                "message": {"content": [{"type": "text", "text": "You've hit your weekly limit"}]}}) + "\n")
        start = {"hook": "SessionStart", "payload": {"session_id": "s9", "transcript_path": transcript, "cwd": "/w",
                                                     "hook_event_name": "SessionStart", "source": "resume"}}
        code, err, inv = h.run(["--resume", "s9"], [[start, {"wait_for_term": True}], [{"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["a", "b"])
        self.assertEqual(exit_codes(inv), [143, 0])
        self.assertEqual(inv[1]["argv"][2:], ["--resume", "s9"])
        self.assertEqual(h.state()["accounts"]["a"]["limited_until"], reset)

    def test_each_claude_launch_records_when_it_started(self):
        # hooks use this to ignore a /login from before the current claude process
        h = Harness(state={"accounts": {"a": {"limited_until": time.time() + 3600}}, "launchers": {}})
        before = time.time()
        code, err, inv = h.run([], [
            [{"snapshot_launchers": True}, {"clear_limits": ["a"]}, stop("s2"), {"wait_for_term": True}],
            [{"snapshot_launchers": True}, {"exit": 0}],
        ])
        self.assertEqual(code, 0, err)
        first, second = (list(i["launchers"].values())[0]["launched_at"] for i in inv)
        self.assertGreaterEqual(first, before)
        self.assertGreater(second, first)

    def test_move_up_survives_claudes_own_rows_after_the_stop_hooks(self):
        # C1 (second review): Claude Code always appends bookkeeping rows after Stop hooks return
        h = Harness(state={"accounts": {"a": {"limited_until": time.time() + 3600}}, "launchers": {}})
        transcript = os.path.join(h.tmp, "s2.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}) + "\n")
        code, err, inv = h.run([], [
            [{"clear_limits": ["a"]}, stop("s2", transcript), {"append": {"path": transcript, "rows": AFTER_STOP_HOOKS}},
             {"wait_for_term": True}],
            [{"exit": 0}],
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["b", "a"])
        self.assertEqual(exit_codes(inv), [143, 0])

    def test_move_up_backs_off_when_the_user_starts_typing(self):
        h = Harness(state={"accounts": {"a": {"limited_until": time.time() + 3600}}, "launchers": {}})
        transcript = os.path.join(h.tmp, "s2.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}) + "\n")
        typed = {"type": "user", "message": {"content": "one more thing"}}
        code, err, inv = h.run([], [
            [{"clear_limits": ["a"]}, stop("s2", transcript), {"append": {"path": transcript, "rows": [typed]}},
             {"sleep": 1.0}, {"exit": 0}],
        ])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["b"])

    def test_no_second_switch_when_rclaude_resumes_onto_the_next_account(self):
        # after a limit switch the resumed conversation still ends on a's limit; SessionStart on b must not switch
        h = Harness(emails={"a": "a@x.com"}, logged_in=("a@x.com", "org-a"))
        transcript = os.path.join(h.tmp, "s1.jsonl")
        with open(transcript, "w") as f:
            for row in ({"type": "user", "message": {"content": "hi"}},
                        {"type": "attachment", "attachment": {"type": "credential_org", "organizationUuid": "org-a"}},
                        {"type": "assistant", "isApiErrorMessage": True, "apiError": "usage_limit_reached",
                         "error": "rate_limit", "timestamp": iso(time.time() - 5),
                         "quotaLimits": {"resetsAt": time.time() + 86400},
                         "message": {"content": [{"type": "text", "text": "You've hit your weekly limit"}]}}):
                f.write(json.dumps(row) + "\n")
        fail = failure("s1")
        fail["payload"]["transcript_path"] = transcript
        resume = {"hook": "SessionStart", "payload": {"session_id": "s1", "transcript_path": transcript, "cwd": "/w",
                                                      "hook_event_name": "SessionStart", "source": "resume"}}
        code, err, inv = h.run([], [[fail, {"wait_for_term": True}], [resume, {"sleep": 0.5}, {"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(accounts(inv), ["a", "b"])
        self.assertNotIn("limited_until", h.state()["accounts"].get("b", {}))

    def test_subcommands_run_once_without_hooks(self):
        h = Harness()
        code, err, inv = h.run(["auth", "status"], [[{"exit": 0}]])
        self.assertEqual(code, 0, err)
        self.assertEqual(inv[0]["argv"], ["auth", "status"])
        self.assertEqual(inv[0]["env"]["CLAUDE_CONFIG_DIR"], h.personal)
        self.assertIsNone(inv[0]["env"]["RCLAUDE_PID"])

    def test_ctrl_c_reaches_claude_not_the_launcher(self):
        # Review focus: the terminal sends SIGINT to rclaude too; it must survive and not traceback.
        h = Harness()
        proc = h.start([], [[{"touch": h.ready}, {"wait_for_term": True}]])
        deadline = time.time() + 10
        while not os.path.exists(h.ready) and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(h.ready))
        proc.send_signal(signal.SIGINT)
        time.sleep(0.3)
        self.assertIsNone(proc.poll(), "rclaude exited on SIGINT")
        proc.send_signal(signal.SIGTERM)  # forwarded to claude
        _, err = proc.communicate(timeout=10)
        self.assertEqual(proc.returncode, 143, err)
        self.assertNotIn("Traceback", err)
