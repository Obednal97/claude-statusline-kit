import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from claude_rotate import hooks
from claude_rotate.config import parse_config
from claude_rotate.state import StateStore

NOW = 1_800_000_000.0
CONFIG = parse_config({"tiers": [
    {"name": "a", "config_dir": "/tmp/p", "auth": "login"},
    {"name": "b", "config_dir": "/tmp/p", "auth": "token"},
    {"name": "work", "config_dir": None, "auth": "login"},
], "min_upswitch_interval_seconds": 120})
FAILURE = {
    "session_id": "s1", "transcript_path": "/t/s1.jsonl", "cwd": "/w", "hook_event_name": "StopFailure",
    "error": "rate_limit", "error_details": "429 Too Many Requests",
    "last_assistant_message": "You've hit your session limit · resets 3:45pm",
}


class HookTestBase(unittest.TestCase):
    def setUp(self):
        self.store = StateStore(tempfile.mkdtemp())
        self.pid = os.getpid()  # must be alive, or the store prunes its launcher entry
        self.kills = []
        self.kill_transcripts = []

    def record(self, pid, transcript=None):
        self.kills.append(pid)
        self.kill_transcripts.append(transcript)

    def env(self, account):
        return {"RCLAUDE_PID": str(self.pid), "RCLAUDE_ACCOUNT": account}

    def seed(self, accounts=None, launcher=None):
        with self.store.locked() as st:
            st["accounts"].update(accounts or {})
            if launcher is not None:
                st["launchers"][str(self.pid)] = launcher


class StopFailureTest(HookTestBase):
    def fail(self, account="a", **overrides):
        payload = dict(FAILURE)
        payload.update(overrides)
        return hooks.handle_stop_failure(payload, self.env(account), CONFIG, self.store, NOW, self.record)

    def test_limit_marks_account_and_requests_switch(self):
        self.seed({"a": {"five_hour": {"pct": 100, "resets_at": NOW + 500, "seen_at": NOW - 10}}})
        req = self.fail()
        acct = self.store.read()["accounts"]["a"]
        self.assertEqual(acct["limited_until"], NOW + 500)
        self.assertIn("session limit", acct["last_error"])
        self.assertEqual((req["reason"], req["target"], req["from_account"]), ("limit", "b", "a"))
        self.assertEqual((req["session_id"], req["transcript_path"]), ("s1", "/t/s1.jsonl"))
        self.assertEqual(self.kills, [self.pid])
        self.assertEqual(self.kill_transcripts, [None])  # a limit kill never backs off
        self.assertEqual(self.store.take_switch(self.pid), req)

    def test_all_limited_records_but_does_not_switch(self):
        self.seed({"b": {"limited_until": NOW + 50}, "work": {"limited_until": NOW + 50}})
        self.assertIsNone(self.fail())
        self.assertGreater(self.store.read()["accounts"]["a"]["limited_until"], NOW)
        self.assertEqual(self.kills, [])
        self.assertFalse(self.store.has_switch(self.pid))

    def test_server_throttle_is_ignored(self):
        self.assertIsNone(self.fail(last_assistant_message="Server is temporarily limiting requests"))
        self.assertEqual(self.store.read()["accounts"], {})
        self.assertEqual(self.kills, [])

    def test_auth_problem_benches_tier_for_an_hour(self):
        req = self.fail(error="account_on_hold", last_assistant_message="Your account is on hold")
        self.assertEqual(self.store.read()["accounts"]["a"]["limited_until"], NOW + 3600)
        self.assertEqual(req["target"], "b")

    def test_unusable_tiers_are_not_targets(self):
        self.seed(launcher={"account": "a", "unusable": ["b"]})
        self.assertEqual(self.fail()["target"], "work")

    def test_pending_request_is_not_duplicated(self):
        self.store.write_switch(self.pid, {"reason": "rebalance"})
        self.assertIsNone(self.fail())
        self.assertEqual(self.kills, [])

    def test_records_which_account_the_session_was_on(self):
        self.fail()
        self.assertEqual(self.store.read()["sessions"]["s1"], {"account": "a", "at": NOW})

    def test_outside_rclaude_does_nothing(self):
        self.assertIsNone(hooks.handle_stop_failure(FAILURE, {}, CONFIG, self.store, NOW, self.record))
        self.assertEqual(self.store.read()["accounts"], {})


class StopTest(HookTestBase):
    def stop(self, account):
        payload = {"session_id": "s2", "transcript_path": "/t/s2.jsonl", "cwd": "/w", "hook_event_name": "Stop"}
        return hooks.handle_stop(payload, self.env(account), CONFIG, self.store, NOW, self.record)

    def test_moves_back_up_after_reset(self):
        self.seed({"a": {"limited_until": NOW - 1}}, {"account": "b", "last_switch_at": NOW - 300})
        req = self.stop("b")
        self.assertEqual((req["reason"], req["target"], req["session_id"]), ("rebalance", "a", "s2"))
        self.assertEqual(self.kills, [self.pid])
        self.assertEqual(self.kill_transcripts, ["/t/s2.jsonl"])  # the kill backs off if the session gets busy

    def test_does_not_switch_while_the_session_is_still_busy(self):
        # A blocking Stop hook (e.g. ralph-loop), background tasks or session crons: not idle.
        self.seed({"a": {"limited_until": NOW + 600}}, {"account": "a", "last_switch_at": 0})
        for busy in ({"stop_hook_active": True}, {"background_tasks": [{"id": "t1"}]}, {"session_crons": [{"id": "c1"}]}):
            payload = {"session_id": "s2", "transcript_path": "/t/s2.jsonl", "cwd": "/w", "hook_event_name": "Stop"}
            payload.update(busy)
            self.assertIsNone(hooks.handle_stop(payload, self.env("a"), CONFIG, self.store, NOW, self.record), busy)
        self.assertEqual(self.kills, [])

    def test_waits_out_the_upswitch_interval(self):
        self.seed({}, {"account": "b", "last_switch_at": NOW - 30})
        self.assertIsNone(self.stop("b"))
        self.assertEqual(self.kills, [])

    def test_moves_down_at_idle_when_another_session_hit_the_limit(self):
        # Review focus: another session already marked "a" limited; ignore the interval.
        self.seed({"a": {"limited_until": NOW + 600}}, {"account": "a", "last_switch_at": NOW - 5})
        self.assertEqual(self.stop("a")["target"], "b")

    def test_stays_on_best(self):
        self.seed({}, {"account": "a", "last_switch_at": 0})
        self.assertIsNone(self.stop("a"))
        self.assertEqual(self.kills, [])

    def test_records_which_account_the_session_was_on(self):
        self.seed({}, {"account": "a", "last_switch_at": 0})
        self.stop("a")
        self.assertEqual(self.store.read()["sessions"]["s2"], {"account": "a", "at": NOW})


RESET = NOW + 4 * 86400
ERR_AT = NOW - 600  # the limit error happened 10 minutes ago


def write_transcript(org=None, resets_at=RESET):
    path = os.path.join(tempfile.mkdtemp(), "s9.jsonl")
    entries = []
    entries.append({"type": "user", "message": {"content": "hi"}})
    if org:  # Claude Code writes it right after the prompt it applies to
        entries.append({"type": "attachment", "attachment": {"type": "credential_org", "organizationUuid": org}})
    entries.append({
        "type": "assistant", "isApiErrorMessage": True, "apiError": "usage_limit_reached", "error": "rate_limit",
        "timestamp": __import__("time").strftime("%Y-%m-%dT%H:%M:%S.000Z", __import__("time").gmtime(ERR_AT)),
        "quotaLimits": {"resetsAt": resets_at, "rateLimitType": "seven_day"},
        "message": {"content": [{"type": "text", "text": "You've hit your weekly limit · resets Oct 16 at 5am"}]},
    })
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")
    return path


class SessionStartTest(HookTestBase):
    """Resuming a conversation that ended on THIS account's limit makes Claude Code open its limit menu
    before anything is sent, so StopFailure never fires: rclaude must notice at SessionStart."""

    def setUp(self):
        super().setUp()
        self.profile = tempfile.mkdtemp()
        with open(os.path.join(self.profile, ".claude.json"), "w") as f:
            json.dump({"oauthAccount": {"emailAddress": "a@x.com", "organizationUuid": "org-a"}}, f)
        self.config = parse_config({"tiers": [
            {"name": "a", "config_dir": self.profile, "auth": "login", "email": "a@x.com"},
            {"name": "b", "config_dir": self.profile, "auth": "token"},
            {"name": "work", "config_dir": None, "auth": "login"},
        ], "min_upswitch_interval_seconds": 120})

    def start(self, account, transcript, source="resume", session="s9"):
        payload = {"session_id": session, "transcript_path": transcript, "cwd": "/w",
                   "hook_event_name": "SessionStart", "source": source}
        return hooks.handle_session_start(payload, self.env(account), self.config, self.store, NOW, self.record)

    def test_limit_from_this_login_switches_before_the_menu_catches_input(self):
        req = self.start("a", write_transcript(org="org-a"))
        self.assertEqual((req["reason"], req["target"], req["session_id"]), ("limit-on-resume", "b", "s9"))
        self.assertEqual(self.store.read()["accounts"]["a"]["limited_until"], RESET)
        self.assertEqual(self.kills, [self.pid])
        self.assertEqual(self.kill_transcripts, [None])

    def test_limit_from_another_org_is_left_alone(self):
        self.assertIsNone(self.start("a", write_transcript(org="org-other")))
        self.assertEqual(self.kills, [])

    def test_unattributable_limit_is_left_alone(self):
        self.assertIsNone(self.start("b", write_transcript(org=None)))
        self.assertEqual(self.kills, [])

    def test_rclaude_record_of_another_account_wins(self):
        with self.store.locked() as st:
            st["sessions"]["s9"] = {"account": "b", "at": ERR_AT + 1}
        self.assertIsNone(self.start("a", write_transcript(org="org-a")))

    def test_rclaude_record_of_this_account_attributes_token_sessions(self):
        with self.store.locked() as st:
            st["sessions"]["s9"] = {"account": "b", "at": ERR_AT + 1}
        req = self.start("b", write_transcript(org=None))
        self.assertEqual(req["target"], "a")

    def test_an_older_record_is_not_trusted_over_the_org(self):
        with self.store.locked() as st:  # rclaude saw this session on b, but before the limit happened
            st["sessions"]["s9"] = {"account": "b", "at": ERR_AT - 100}
        self.assertEqual(self.start("a", write_transcript(org="org-a"))["target"], "b")

    def test_pending_switch_is_left_alone(self):
        self.store.write_switch(self.pid, {"reason": "rebalance"})
        self.assertIsNone(self.start("a", write_transcript(org="org-a")))
        self.assertEqual(self.kills, [])

    def test_new_sessions_and_clean_transcripts_do_nothing(self):
        self.assertIsNone(self.start("a", write_transcript(org="org-a"), source="startup"))
        self.assertIsNone(self.start("a", write_transcript(org="org-a", resets_at=NOW - 1)))
        self.assertEqual(self.kills, [])

    def test_nowhere_to_go_marks_the_limit_but_stays(self):
        self.seed({"b": {"limited_until": NOW + 50}, "work": {"limited_until": NOW + 50}})
        self.assertIsNone(self.start("a", write_transcript(org="org-a")))
        self.assertEqual(self.store.read()["accounts"]["a"]["limited_until"], RESET)
        self.assertEqual(self.kills, [])


class LoginInsideSessionTest(HookTestBase):
    """A /login typed inside an rclaude session changes that session's account (and the profile's login).
    The limit must be blamed on the account actually used, not on the tier the session was launched as."""

    def setUp(self):
        super().setUp()
        self.profile = tempfile.mkdtemp()
        with open(os.path.join(self.profile, ".claude.json"), "w") as f:  # /login wrote another account here
            json.dump({"oauthAccount": {"emailAddress": "a@x.com", "organizationUuid": "org-a"}}, f)
        self.config = parse_config({"tiers": [
            {"name": "a", "config_dir": self.profile, "auth": "login", "email": "a@x.com"},
            {"name": "b", "config_dir": self.profile, "auth": "token", "email": "b@x.com"},
            {"name": "work", "config_dir": None, "auth": "login"},
        ], "min_upswitch_interval_seconds": 120})
        self.launched = NOW - 300

    def transcript(self, login_at):
        path = os.path.join(tempfile.mkdtemp(), "s1.jsonl")
        ts = __import__("time").strftime("%Y-%m-%dT%H:%M:%S.000Z", __import__("time").gmtime(login_at))
        with open(path, "w") as f:
            f.write(json.dumps({"type": "user", "timestamp": ts,
                                "message": {"content": "<local-command-stdout>Login successful</local-command-stdout>"}}) + "\n")
        return path

    def fail_on_b(self, login_at):
        self.seed({}, {"account": "b", "launched_at": self.launched, "last_switch_at": 0})
        payload = dict(FAILURE, transcript_path=self.transcript(login_at))
        return hooks.handle_stop_failure(payload, self.env("b"), self.config, self.store, NOW, self.record)

    def test_limit_after_a_login_is_blamed_on_the_logged_in_account(self):
        req = self.fail_on_b(login_at=self.launched + 60)
        accounts = self.store.read()["accounts"]
        self.assertGreater(accounts["a"]["limited_until"], NOW)
        self.assertNotIn("limited_until", accounts.get("b", {}))
        # relaunching on b restores b's own token, so b is the right target even though it is the current tier
        self.assertEqual((req["reason"], req["target"]), ("limit", "b"))
        self.assertEqual(self.kills, [self.pid])

    def test_the_session_is_recorded_under_the_blamed_account(self):
        self.fail_on_b(login_at=self.launched + 60)
        self.assertEqual(self.store.read()["sessions"]["s1"]["account"], "a")

    def test_login_into_an_unknown_account_records_nothing(self):
        with open(os.path.join(self.profile, ".claude.json"), "w") as f:
            json.dump({"oauthAccount": {"emailAddress": "stranger@x.com"}}, f)
        self.fail_on_b(login_at=self.launched + 60)
        self.assertNotIn("s1", self.store.read()["sessions"])

    def test_stop_after_a_login_records_the_logged_in_account(self):
        self.seed({}, {"account": "b", "launched_at": self.launched, "last_switch_at": 0})
        payload = {"session_id": "s1", "transcript_path": self.transcript(self.launched + 60), "cwd": "/w",
                   "hook_event_name": "Stop"}
        hooks.handle_stop(payload, self.env("b"), self.config, self.store, NOW, self.record)
        self.assertEqual(self.store.read()["sessions"]["s1"]["account"], "a")

    def test_launched_since_uses_the_latest_known_launch(self):
        self.assertEqual(hooks.launched_since({"started_at": 5, "last_switch_at": 9}), 9)
        self.assertEqual(hooks.launched_since({"started_at": 5, "last_switch_at": 9, "launched_at": 12}), 12)

    def test_login_before_this_launch_does_not_count(self):
        self.fail_on_b(login_at=self.launched - 60)
        self.assertGreater(self.store.read()["accounts"]["b"]["limited_until"], NOW)


class KillHelperTest(unittest.TestCase):
    def test_finds_the_direct_child_of_the_launcher(self):
        code = "import subprocess; p = subprocess.Popen(['sleep', '30']); print(p.pid, flush=True); p.wait()"
        middle = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
        grandchild = int(middle.stdout.readline())
        try:
            self.assertEqual(hooks.find_launcher_child(os.getpid(), grandchild), middle.pid)
            self.assertIsNone(hooks.find_launcher_child(999999, grandchild))
        finally:
            os.kill(grandchild, 15)
            middle.wait()
            middle.stdout.close()

    def test_finds_claude_when_the_launcher_is_pid_1(self):
        # In a container, rclaude can be the entrypoint (PID 1); claude is then a direct child of PID 1.
        parents = {500: 400, 400: 1}
        with mock.patch.object(hooks, "parent_pid", parents.get):
            self.assertEqual(hooks.find_launcher_child(1, 500), 400)
            self.assertIsNone(hooks.find_launcher_child(777, 500))

    def test_delayed_kill_backs_off_when_the_transcript_grew(self):
        transcript = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        with open(transcript, "w") as f:
            f.write("x\n")
        size = os.path.getsize(transcript)
        with open(transcript, "a") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "the user typed"}}) + "\n")
        child = subprocess.Popen(["sleep", "30"])
        try:
            self.assertFalse(hooks.delayed_kill(os.getpid(), child.pid, 0, transcript, size))
            self.assertIsNone(child.poll())
            self.assertTrue(hooks.delayed_kill(os.getpid(), child.pid, 0, transcript, os.path.getsize(transcript)))
            self.assertEqual(child.wait(timeout=5), -15)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()

    def test_unschedulable_kill_discards_the_request(self):
        store = StateStore(tempfile.mkdtemp())
        store.write_switch(999999, {"reason": "limit"})
        hooks.schedule_kill(999999, "/nonexistent/rclaude", store)  # 999999 is nobody's parent
        self.assertFalse(store.has_switch(999999))

    def transcript_with(self, *appended):
        path = os.path.join(tempfile.mkdtemp(), "t.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}) + "\n")
        size = os.path.getsize(path)
        with open(path, "a") as f:
            for row in appended:
                f.write(json.dumps(row) + "\n")
        return path, size

    def test_bookkeeping_rows_after_the_stop_hooks_do_not_block_the_kill(self):
        # Claude Code always appends these after the Stop hooks return (C1 from the second review)
        path, size = self.transcript_with({"type": "system", "subtype": "stop_hook_summary"},
                                          {"type": "system", "subtype": "turn_duration"})
        child = subprocess.Popen(["sleep", "30"])
        self.assertTrue(hooks.delayed_kill(os.getpid(), child.pid, 0, path, size))
        self.assertEqual(child.wait(timeout=5), -15)

    def test_a_new_prompt_or_reply_after_the_stop_backs_off(self):
        for row in ({"type": "user", "message": {"content": "next question"}},
                    {"type": "assistant", "message": {"content": [{"type": "text", "text": "continuing"}]}}):
            path, size = self.transcript_with(row)
            child = subprocess.Popen(["sleep", "30"])
            try:
                self.assertFalse(hooks.delayed_kill(os.getpid(), child.pid, 0, path, size), row["type"])
                self.assertIsNone(child.poll())
            finally:
                child.kill()
                child.wait()

    def test_delayed_kill_only_hits_a_matching_child(self):
        child = subprocess.Popen(["sleep", "30"])
        self.assertFalse(hooks.delayed_kill(999999, child.pid, 0))
        self.assertIsNone(child.poll())
        self.assertTrue(hooks.delayed_kill(os.getpid(), child.pid, 0))
        self.assertEqual(child.wait(timeout=5), -15)
