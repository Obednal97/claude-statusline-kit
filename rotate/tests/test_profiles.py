import json
import os
import tempfile
import unittest
from unittest import mock

from claude_rotate.config import Tier
from claude_rotate.profiles import login_mismatch, profile_account


def profile_dir(email="a@x.com", org="org-a"):
    d = tempfile.mkdtemp()
    if email is not None:
        with open(os.path.join(d, ".claude.json"), "w") as f:
            json.dump({"oauthAccount": {"emailAddress": email, "organizationUuid": org}, "other": 1}, f)
    return d


class ProfileAccountTest(unittest.TestCase):
    def test_reads_email_and_org_from_the_profile(self):
        acct = profile_account(Tier("a", profile_dir(), "login"))
        self.assertEqual((acct.email, acct.org), ("a@x.com", "org-a"))

    def test_default_profile_reads_home_dot_claude_json(self):
        home = tempfile.mkdtemp()
        with open(os.path.join(home, ".claude.json"), "w") as f:
            json.dump({"oauthAccount": {"emailAddress": "work@co.com", "organizationUuid": "org-w"}}, f)
        with mock.patch.dict(os.environ, {"HOME": home}):
            self.assertEqual(profile_account(Tier("work", None, "login")).email, "work@co.com")

    def test_missing_profile(self):
        self.assertIsNone(profile_account(Tier("a", profile_dir(email=None), "login")))


class LoginMismatchTest(unittest.TestCase):
    def test_matching_login_is_fine_ignoring_case(self):
        self.assertIsNone(login_mismatch(Tier("a", profile_dir("A@X.com"), "login", "a@x.com")))

    def test_wrong_login_explains_the_fix(self):
        d = profile_dir("someone@else.com")
        problem = login_mismatch(Tier("a", d, "login", "a@x.com"))
        for fragment in ("someone@else.com", "a@x.com", "CLAUDE_CONFIG_DIR=%s claude" % d, "/login as a@x.com"):
            self.assertIn(fragment, problem)

    def test_nobody_logged_in(self):
        self.assertIn("nobody", login_mismatch(Tier("a", profile_dir(email=None), "login", "a@x.com")))

    def test_token_tiers_and_tiers_without_email_are_not_checked(self):
        self.assertIsNone(login_mismatch(Tier("b", profile_dir("other@x.com"), "token", "b@x.com")))
        self.assertIsNone(login_mismatch(Tier("a", profile_dir("other@x.com"), "login")))
