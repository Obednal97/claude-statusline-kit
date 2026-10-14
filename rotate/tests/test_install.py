import os
import subprocess
import sys
import tempfile
import unittest

ROTATE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(ROTATE, "install.sh")
UNINSTALL = os.path.join(ROTATE, "uninstall.sh")
ENTRY = os.path.realpath(os.path.join(ROTATE, "rclaude"))


class InstallerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.home = os.path.join(self.root, "home with space")  # Review focus: quoting
        self.fakebin = os.path.join(self.root, "bin")
        os.makedirs(self.home)
        os.makedirs(self.fakebin)
        self.tool("claude", "#!/bin/sh\nexit 0\n")
        self.link = os.path.join(self.home, ".local", "bin", "rclaude")
        self.config = os.path.join(self.home, ".config", "claude-rotate", "accounts.json")

    def tool(self, name, body):
        path = os.path.join(self.fakebin, name)
        with open(path, "w") as f:
            f.write(body)
        os.chmod(path, 0o755)

    def run_script(self, script):
        path = os.pathsep.join([self.fakebin, os.path.dirname(sys.executable), "/usr/bin", "/bin"])
        return subprocess.run(["bash", script], env={"HOME": self.home, "PATH": path}, cwd=self.root,
                              capture_output=True, text=True, timeout=60)

    def test_fresh_install(self):
        p = self.run_script(INSTALL)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(os.path.islink(self.link))
        target = os.readlink(self.link)
        self.assertTrue(os.path.isabs(target))  # Review focus: run from outside the repo
        self.assertEqual(target, ENTRY)
        with open(self.config) as f, open(os.path.join(ROTATE, "accounts.example.json")) as g:
            self.assertEqual(f.read(), g.read())
        self.assertIn("is not on your PATH", p.stdout + p.stderr)
        self.assertIn("rclaude status", p.stdout)

    def test_rerun_keeps_config_and_link(self):
        self.run_script(INSTALL)
        with open(self.config, "w") as f:
            f.write('{"mine": true}')
        p = self.run_script(INSTALL)
        self.assertEqual(p.returncode, 0, p.stderr)
        with open(self.config) as f:
            self.assertEqual(f.read(), '{"mine": true}')
        self.assertEqual(os.readlink(self.link), ENTRY)
        self.assertIn("Keeping your existing", p.stdout)

    def test_existing_file_is_backed_up(self):
        os.makedirs(os.path.dirname(self.link))
        with open(self.link, "w") as f:
            f.write("old")
        p = self.run_script(INSTALL)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.readlink(self.link), ENTRY)
        backups = [n for n in os.listdir(os.path.dirname(self.link)) if n.startswith("rclaude.bak-")]
        self.assertEqual(len(backups), 1)
        with open(os.path.join(os.path.dirname(self.link), backups[0])) as f:
            self.assertEqual(f.read(), "old")

    def test_link_to_another_checkout_is_replaced(self):
        # Review focus: the owner's link points at an older checkout; it moves here and says where it was.
        os.makedirs(os.path.dirname(self.link))
        os.symlink("/elsewhere/rclaude", self.link)
        p = self.run_script(INSTALL)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.readlink(self.link), ENTRY)
        self.assertIn("/elsewhere/rclaude", p.stdout)

    def test_no_path_warning_when_local_bin_is_on_path(self):
        os.makedirs(os.path.dirname(self.link))
        path = os.pathsep.join([os.path.dirname(self.link), self.fakebin, os.path.dirname(sys.executable),
                                "/usr/bin", "/bin"])
        p = subprocess.run(["bash", INSTALL], env={"HOME": self.home, "PATH": path}, cwd=self.root,
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("is not on your PATH", p.stdout + p.stderr)

    def test_missing_claude_only_warns(self):
        os.remove(os.path.join(self.fakebin, "claude"))
        p = self.run_script(INSTALL)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("claude is not on your PATH", p.stdout + p.stderr)

    def test_windows_is_refused(self):
        self.tool("uname", "#!/bin/sh\necho MINGW64_NT-10.0-19045\n")
        p = self.run_script(INSTALL)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Windows isn't supported", p.stderr)
        self.assertFalse(os.path.lexists(self.link))

    def test_old_python_is_refused(self):
        self.tool("python3", "#!/bin/sh\nexit 1\n")
        p = self.run_script(INSTALL)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("3.9", p.stderr)
        self.assertFalse(os.path.lexists(self.link))

    def test_uninstall_removes_only_our_link(self):
        self.run_script(INSTALL)
        p = self.run_script(UNINSTALL)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertFalse(os.path.lexists(self.link))
        self.assertTrue(os.path.exists(self.config))
        self.assertIn("rm -r ~/.config/claude-rotate", p.stdout)
        os.symlink("/elsewhere/rclaude", self.link)
        p = self.run_script(UNINSTALL)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(os.readlink(self.link), "/elsewhere/rclaude")
        self.assertIn("Left", p.stdout)
