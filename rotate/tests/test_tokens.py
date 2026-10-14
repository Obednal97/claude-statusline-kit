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
        tokens.set_token("spare", "sk-ant-oat01-abc_DEF-123")
        self.assertEqual(tokens.get_token("spare"), "sk-ant-oat01-abc_DEF-123")
        self.assertIsNone(tokens.get_token("other"))
        self.assertEqual(mode(self.path), 0o600)
        self.assertEqual(mode(os.path.dirname(self.path)), 0o700)

    def test_keeps_other_tokens_and_tightens_a_loose_file(self):
        # Review focus: a tokens.json someone edited by hand (0644) ends up 0600, other entries kept.
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"first": "tok-1"}, f)
        os.chmod(self.path, 0o644)
        tokens.set_token("spare", "tok-2")
        with open(self.path) as f:
            self.assertEqual(json.load(f), {"first": "tok-1", "spare": "tok-2"})
        self.assertEqual(mode(self.path), 0o600)

    def test_no_temp_files_left_behind(self):
        tokens.set_token("spare", "tok-2")
        tokens.set_token("spare", "tok-3")
        self.assertEqual(os.listdir(os.path.dirname(self.path)), ["tokens.json"])

    def test_corrupt_file_raises(self):
        os.makedirs(os.path.dirname(self.path))
        for content in ("{not json", "[1, 2]"):
            with open(self.path, "w") as f:
                f.write(content)
            with self.assertRaises(tokens.TokenStoreError):
                tokens.get_token("spare")
            with self.assertRaises(tokens.TokenStoreError):
                tokens.set_token("spare", "tok")

    def test_rejects_odd_tokens_and_saves_nothing(self):
        for bad in ["", "has space", 'quote"', "new\nline", "trailing\n"]:
            with self.assertRaises(tokens.TokenStoreError):
                tokens.set_token("spare", bad)
        self.assertFalse(os.path.exists(self.path))

    def test_rejects_odd_names(self):
        with self.assertRaises(tokens.TokenStoreError):
            tokens.set_token('b"; rm', "sk-ant-oat01-x")

    def test_describe_names_the_file(self):
        self.assertEqual(tokens.describe("spare"), "%s (as spare)" % self.path)


class BackendChoiceTest(unittest.TestCase):
    def test_override_wins_on_macos_too(self):
        with mock.patch.dict(os.environ, {"RCLAUDE_TOKEN_FILE": "/x/t.json"}), \
                mock.patch.object(tokens.sys, "platform", "darwin"):
            self.assertEqual(tokens.token_file(), "/x/t.json")

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
            out = "tok-xyz\n" if cmd[:2] == ["security", "find-generic-password"] else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

        with mock.patch.dict(os.environ, env_without_override(USER="u"), clear=True), \
                mock.patch.object(tokens.sys, "platform", "darwin"), \
                mock.patch.object(tokens.subprocess, "run", fake_run):
            tokens.set_token("spare", "tok-xyz")
        self.assertTrue(all("tok-xyz" not in " ".join(cmd) for cmd, _ in calls))
        self.assertEqual(calls[0][0], ["security", "-i"])
        self.assertIn('-s "claude-rotate:spare" -a "u" -w "tok-xyz"', calls[0][1])

    def test_service_name(self):
        self.assertEqual(tokens.service_name("spare"), "claude-rotate:spare")
