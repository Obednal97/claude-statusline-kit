"""The device row (statusline/device.sh) and the installer's row choice (install.sh --rows)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEVICE = os.path.join(KIT, "statusline", "device.sh")
INSTALL = os.path.join(KIT, "install.sh")
ANSI = re.compile(r"\x1b\[[0-9;]*m")
GREEN, YELLOW, RED = "\x1b[38;2;63;185;80m", "\x1b[38;2;210;153;34m", "\x1b[38;2;248;81;73m"


@unittest.skipUnless(shutil.which("node") and shutil.which("bash"), "needs node and bash")
class DeviceWidgetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_widget(self, sample=None, **extra):
        env = {"PATH": os.environ["PATH"], "HOME": os.path.expanduser("~"), "TMPDIR": self.tmp,
               "USER": "tester", "COLORTERM": "truecolor"}
        if sample is not None:
            env["STATUSLINE_DEVICE_SAMPLE"] = json.dumps(sample)
        env.update(extra)
        p = subprocess.run(["bash", DEVICE], env=env, input="{}", capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def segments(self, out):
        return out.split(" | ")

    def test_bars_and_percentages(self):
        out = self.run_widget({"cpu": 36, "load15": 1, "cores": 15, "mem": 69, "disk": 81})
        self.assertEqual(ANSI.sub("", out), "CPU ██░░░░ 36% | Mem ████░░ 69% | Disk █████░ 81%")

    def test_fits_a_phone_terminal_when_everything_is_full(self):
        # ccstatusline flexMode full leaves 53 columns on a 59-column terminal.
        out = self.run_widget({"cpu": 100, "load15": 15, "cores": 15, "mem": 100, "disk": 100})
        self.assertLessEqual(len(ANSI.sub("", out)), 53)

    def test_colour_thresholds(self):
        out = self.segments(self.run_widget({"cpu": 59, "load15": 0, "cores": 8, "mem": 60, "disk": 85}))
        self.assertIn(GREEN, out[0])
        self.assertIn(YELLOW, out[1])
        self.assertIn(RED, out[2])

    def test_long_moderate_load_turns_cpu_yellow(self):
        # The case that prompted the row: an emulator holding ~5 of 15 cores for two days.
        calm = self.segments(self.run_widget({"cpu": 33, "load15": 4, "cores": 15, "mem": 10, "disk": 10}))
        stuck = self.segments(self.run_widget({"cpu": 33, "load15": 5, "cores": 15, "mem": 10, "disk": 10}))
        self.assertIn(GREEN, calm[0])
        self.assertIn(YELLOW, stuck[0])
        self.assertIn(GREEN, stuck[1])   # the load rule touches CPU only

    def test_no_load_average_means_current_cpu_only(self):
        # Windows: os.loadavg() is all zeros.
        out = self.segments(self.run_widget({"cpu": 20, "load15": 0, "cores": 8, "mem": 10, "disk": 10}))
        self.assertIn(GREEN, out[0])

    def test_missing_readings_are_left_out(self):
        out = self.run_widget({"cpu": 20, "load15": 0, "cores": 8, "mem": None, "disk": 50})
        self.assertEqual(ANSI.sub("", out), "CPU █░░░░░ 20% | Disk ███░░░ 50%")

    def test_real_readings(self):
        pattern = r"^CPU [█░]{6} \d{1,3}% \| Mem [█░]{6} \d{1,3}% \| Disk [█░]{6} \d{1,3}%$"
        start = time.time()
        first = ANSI.sub("", self.run_widget())
        self.assertRegex(first, pattern)
        self.assertLess(time.time() - start, 5)
        self.assertTrue(os.path.exists(os.path.join(self.tmp, "ccstatusline-device-tester.json")))

    def test_render_straight_after_another_reuses_its_cpu_figure(self):
        # Under a second apart, a fresh CPU sample would be noise.
        with open(os.path.join(self.tmp, "ccstatusline-device-tester.json"), "w") as f:
            json.dump({"t": time.time() * 1000, "idle": 0, "total": 0, "pct": 42}, f)
        self.assertTrue(ANSI.sub("", self.run_widget()).startswith("CPU ███░░░ 42% | "))


@unittest.skipUnless(shutil.which("node") and shutil.which("bash"), "needs node and bash")
class InstallRowsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.home = os.path.join(self.root, "home with space")
        self.bin = os.path.join(self.root, "bin")
        os.makedirs(self.home)
        os.makedirs(self.bin)
        for name in ("ccstatusline", "ccusage"):
            path = os.path.join(self.bin, name)
            with open(path, "w") as f:
                f.write("#!/bin/sh\nexit 0\n")
            os.chmod(path, 0o755)
        node_dir = os.path.dirname(shutil.which("node"))
        self.env = {"HOME": self.home, "PATH": os.pathsep.join([self.bin, node_dir, "/usr/bin", "/bin"])}
        self.cfg = os.path.join(self.home, ".config", "ccstatusline")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def install(self, *args):
        return subprocess.run(["bash", INSTALL] + list(args), env=self.env, cwd=self.root, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=60)

    def rows(self):
        with open(os.path.join(self.cfg, "settings.json")) as f:
            lines = json.load(f)["lines"]
        return [os.path.basename(next(w["commandPath"] for w in line if w.get("commandPath"))) for line in lines]

    def test_all_rows_without_a_terminal(self):
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.rows(), ["session-cost.sh", "model.sh", "repo-name.sh", "account.sh", "device.sh"])
        self.assertIn("Rows: Money, Context, Git, Account, Device", p.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.cfg, "device.sh")))

    def test_rows_flag_is_saved_and_kept(self):
        p = self.install("--rows", "5, 1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.rows(), ["session-cost.sh", "device.sh"])
        with open(os.path.join(self.cfg, "kit-rows")) as f:
            self.assertEqual(f.read(), "1,5\n")
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self.rows(), ["session-cost.sh", "device.sh"])
        p = self.install("--rows=2,3")
        self.assertEqual(self.rows(), ["model.sh", "repo-name.sh"])

    def test_bad_rows_change_nothing(self):
        self.install("--rows", "1,5")
        for bad in ("6", "0", "1,x", "*", ", "):
            p = self.install("--rows", bad)
            self.assertNotEqual(p.returncode, 0, bad)
            self.assertIn("--rows takes row numbers", p.stderr)
            self.assertEqual(self.rows(), ["session-cost.sh", "device.sh"])
        # a refused run stops before backing anything up
        self.assertEqual([n for n in os.listdir(self.cfg) if n.startswith("settings.json.bak-")], [])

    def test_help(self):
        p = self.install("--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("5 Device", p.stdout)
        self.assertFalse(os.path.exists(self.cfg))

    @unittest.skipIf(sys.platform == "win32", "needs a pty")
    def test_asks_in_a_terminal(self):
        import pty
        self.install("--rows", "1,2,3")
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(self.root)
            os.execve("/bin/bash" if os.path.exists("/bin/bash") else shutil.which("bash"),
                      ["bash", INSTALL], self.env)
        out = b""
        # Toggle 3 off and 5 on, try an invalid answer, then accept.
        for reply in (b"9\n", b"3 5\n", b"\n"):
            out += self._read_until(fd, b"switch them on or off: ")
            os.write(fd, reply)
        out += self._read_until(fd, None)
        os.waitpid(pid, 0)
        text = out.decode("utf8", "replace")
        self.assertIn("[x] 1 Money", text)
        self.assertIn("[ ] 5 Device", text)
        self.assertIn("Type numbers from 1 to 5", text)
        self.assertEqual(self.rows(), ["session-cost.sh", "model.sh", "device.sh"])

    def _read_until(self, fd, marker, timeout=30):
        buf, end = b"", time.time() + timeout
        while time.time() < end:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            if marker and buf.endswith(marker):
                break
        return buf


if __name__ == "__main__":
    unittest.main()
