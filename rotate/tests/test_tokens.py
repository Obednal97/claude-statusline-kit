import json
import os
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

from claude_rotate import tokens


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def fake_token(tag, length=107):
    """A made-up token shaped like a whole `claude setup-token` one (built, so the public-repo scan stays clean)."""
    head = tokens.SETUP_TOKEN_PREFIX + tag + "_"
    return head + "x" * (length - len(head))


TOK_1, TOK_2, TOK_3 = fake_token("one"), fake_token("two"), fake_token("three")


def env_without_override(**extra):
    env = {k: v for k, v in os.environ.items() if k != "RCLAUDE_TOKEN_FILE"}
    env.update(extra)
    return env


class TokenFileTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "sub", "tokens.json")
        patcher = mock.patch.dict(os.environ, {"RCLAUDE_TOKEN_FILE": self.path})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_file_means_no_token(self):
        self.assertIsNone(tokens.get_token("spare"))

    def test_round_trip_creates_private_folder_and_file(self):
        tokens.set_token("spare", TOK_1)
        self.assertEqual(tokens.get_token("spare"), TOK_1)
        self.assertIsNone(tokens.get_token("other"))
        self.assertEqual(mode(self.path), 0o600)
        self.assertEqual(mode(os.path.dirname(self.path)), 0o700)

    def test_keeps_other_tokens_and_tightens_a_loose_file(self):
        # Review focus: a tokens.json someone edited by hand (0644) ends up 0600, other entries kept.
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"first": TOK_1}, f)
        os.chmod(self.path, 0o644)
        tokens.set_token("spare", TOK_2)
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"first": TOK_1, "spare": TOK_2})
        self.assertEqual(mode(self.path), 0o600)

    def test_no_temp_files_left_behind(self):
        tokens.set_token("spare", TOK_2)
        tokens.set_token("spare", TOK_3)
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["tokens.json"])

    def test_corrupt_file_raises(self):
        os.makedirs(os.path.dirname(self.path))
        for content in ("{not json", "[1, 2]"):
            with open(self.path, "w") as f:
                f.write(content)
            with self.assertRaises(tokens.TokenStoreError):
                tokens.get_token("spare")
            with self.assertRaises(tokens.TokenStoreError):
                tokens.set_token("spare", TOK_1)

    def test_rejects_odd_tokens_and_saves_nothing(self):
        for bad in ["", "has space", 'quote"', "new\nline", "trailing\n"]:
            with self.assertRaises(tokens.TokenStoreError):
                tokens.set_token("spare", bad)
        self.assertFalse(os.path.exists(self.path))

    def test_rejects_a_token_cut_at_a_terminal_wrap(self):
        # A token that wraps onto two lines arrives as the first line only (getpass reads one); saved, it would
        # get a 401 from Claude Code later and rclaude would bench the account for an hour.
        for cut in (79, 80, tokens.SETUP_TOKEN_MIN_LEN - 1):
            with self.assertRaises(tokens.TokenStoreError) as caught:
                tokens.set_token("spare", fake_token("cut")[:cut])
            message = str(caught.exception)
            self.assertIn("cut short", message)
            self.assertIn("nothing was saved", message)
            self.assertIn("rclaude add-token spare again", message)
        self.assertFalse(os.path.exists(self.path))

    def test_rejects_other_kinds_of_token_and_keeps_the_old_one(self):
        tokens.set_token("spare", TOK_1)
        for bad in ("sk-ant-api03-" + "x" * 95, "x" * 108, tokens.SETUP_TOKEN_PREFIX.upper() + "x" * 95):
            with self.assertRaises(tokens.TokenStoreError) as caught:
                tokens.set_token("spare", bad)
            self.assertIn("isn't a `claude setup-token` token", str(caught.exception))
        self.assertEqual(tokens.get_token("spare"), TOK_1)

    def test_accepts_whole_and_longer_tokens(self):
        # A floor, not an exact length: a longer token from a future format still goes in.
        for good in (fake_token("min", tokens.SETUP_TOKEN_MIN_LEN), fake_token("full"), fake_token("long", 200)):
            tokens.set_token("spare", good)
            self.assertEqual(tokens.get_token("spare"), good)

    def test_rejects_odd_names(self):
        with self.assertRaises(tokens.TokenStoreError):
            tokens.set_token('b"; rm', TOK_1)

    def test_describe_names_the_file(self):
        self.assertEqual(tokens.describe("spare"), "%s (as spare)" % self.path)


class BackendChoiceTest(unittest.TestCase):
    def test_override_wins_on_macos_too(self):
        with mock.patch.dict(os.environ, {"RCLAUDE_TOKEN_FILE": "/x/t.json"}), \
                mock.patch.object(tokens.sys, "platform", "darwin"):
            self.assertEqual(tokens.token_file(), "/x/t.json")

    def test_override_path_is_expanded_and_absolute(self):
        # Final review: a quoted "~" or a relative path must not drop a token file into the current project folder.
        with mock.patch.dict(os.environ, {"RCLAUDE_TOKEN_FILE": "~/t.json", "HOME": "/home/u"}):
            self.assertEqual(tokens.token_file(), "/home/u/t.json")
        with mock.patch.dict(os.environ, {"RCLAUDE_TOKEN_FILE": "rel/t.json"}):
            self.assertEqual(tokens.token_file(), os.path.join(os.getcwd(), "rel", "t.json"))

    def test_macos_uses_the_keychain(self):
        with mock.patch.dict(os.environ, env_without_override(), clear=True), \
                mock.patch.object(tokens.sys, "platform", "darwin"):
            self.assertIsNone(tokens.token_file())
            self.assertEqual(tokens.describe("spare"), "the Keychain as claude-rotate:spare")

    def test_linux_uses_a_file_in_the_config_folder(self):
        with mock.patch.dict(os.environ, env_without_override(HOME="/home/u"), clear=True), \
                mock.patch.object(tokens.sys, "platform", "linux"):
            self.assertEqual(tokens.token_file(), "/home/u/.config/claude-rotate/tokens.json")

    def test_keychain_token_goes_on_stdin_not_argv(self):
        calls = []

        def fake_run(cmd, input=None, **kwargs):
            calls.append((cmd, input))
            out = TOK_1 + "\n" if cmd[:2] == ["security", "find-generic-password"] else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

        with mock.patch.dict(os.environ, env_without_override(USER="u"), clear=True), \
                mock.patch.object(tokens.sys, "platform", "darwin"), \
                mock.patch.object(tokens.subprocess, "run", fake_run):
            tokens.set_token("spare", TOK_1)
        self.assertTrue(all(TOK_1 not in " ".join(cmd) for cmd, _ in calls))
        self.assertEqual(calls[0][0], ["security", "-i"])
        self.assertIn('-s "claude-rotate:spare" -a "u" -w "%s"' % TOK_1, calls[0][1])

    def test_keychain_is_not_touched_for_a_cut_token(self):
        with mock.patch.dict(os.environ, env_without_override(USER="u"), clear=True), \
                mock.patch.object(tokens.sys, "platform", "darwin"), \
                mock.patch.object(tokens.subprocess, "run") as run:
            with self.assertRaises(tokens.TokenStoreError):
                tokens.set_token("spare", TOK_1[:80])
        run.assert_not_called()

    def test_service_name(self):
        self.assertEqual(tokens.service_name("spare"), "claude-rotate:spare")
