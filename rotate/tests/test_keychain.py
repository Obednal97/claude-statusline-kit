import os
import tempfile
import unittest
from unittest import mock

from claude_rotate import keychain


class FakeKeychainTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "kc.json")
        patcher = mock.patch.dict(os.environ, {"RCLAUDE_FAKE_KEYCHAIN": self.path})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_missing_file_means_no_token(self):
        self.assertIsNone(keychain.get_token("b"))

    def test_round_trip(self):
        keychain.set_token("b", "sk-ant-oat01-abc_DEF-123")
        self.assertEqual(keychain.get_token("b"), "sk-ant-oat01-abc_DEF-123")
        self.assertIsNone(keychain.get_token("other"))

    def test_rejects_odd_tokens_and_saves_nothing(self):
        for bad in ["", "has space", 'quote"', "new\nline", "trailing\n"]:
            with self.assertRaises(keychain.KeychainError):
                keychain.set_token("b", bad)
        self.assertFalse(os.path.exists(self.path))

    def test_rejects_odd_names(self):
        with self.assertRaises(keychain.KeychainError):
            keychain.set_token('b"; rm', "sk-ant-oat01-x")

    def test_service_name(self):
        self.assertEqual(keychain.service_name("spare"), "claude-rotate:spare")
