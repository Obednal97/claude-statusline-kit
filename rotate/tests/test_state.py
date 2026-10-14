import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from claude_rotate.state import StateStore, default_state, pid_alive

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class StateStoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.store = StateStore(self.dir)

    def test_missing_state_reads_as_default(self):
        self.assertEqual(self.store.read(), default_state())

    def test_locked_changes_are_saved_privately(self):
        with self.store.locked() as st:
            st["accounts"]["a"] = {"limited_until": 5}
        self.assertEqual(self.store.read()["accounts"], {"a": {"limited_until": 5}})
        self.assertEqual(os.stat(self.store.state_path).st_mode & 0o777, 0o600)

    def test_exception_inside_locked_does_not_save(self):
        with self.assertRaises(RuntimeError):
            with self.store.locked() as st:
                st["accounts"]["a"] = {}
                raise RuntimeError("boom")
        self.assertEqual(self.store.read()["accounts"], {})

    def test_corrupt_state_is_moved_aside(self):
        with open(self.store.state_path, "w") as f:
            f.write("{broken")
        self.assertEqual(self.store.read(), default_state())
        moved = [n for n in os.listdir(self.dir) if n.startswith("state.json.corrupt-")]
        self.assertEqual(len(moved), 1)
        with open(self.store.log_path) as f:
            self.assertIn("unreadable", f.read())

    def test_wrong_shape_counts_as_corrupt(self):
        with open(self.store.state_path, "w") as f:
            json.dump({"accounts": []}, f)
        self.assertEqual(self.store.read(), default_state())

    def test_dead_launchers_are_pruned_on_save(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        with self.store.locked() as st:
            st["launchers"][str(os.getpid())] = {"account": "a"}
            st["launchers"][str(dead.pid)] = {"account": "b"}
        self.assertEqual(list(self.store.read()["launchers"]), [str(os.getpid())])

    def test_concurrent_writers_do_not_lose_updates(self):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "from claude_rotate.state import StateStore\n"
            "s = StateStore(%r)\n"
            "for _ in range(25):\n"
            "    with s.locked() as st:\n"
            "        a = st['accounts'].setdefault('n', {'count': 0}); a['count'] += 1\n"
        ) % (ROOT, self.dir)
        procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(4)]
        for p in procs:
            self.assertEqual(p.wait(), 0)
        self.assertEqual(self.store.read()["accounts"]["n"]["count"], 100)

    def test_switch_requests(self):
        self.assertFalse(self.store.has_switch(123))
        self.assertIsNone(self.store.take_switch(123))
        self.store.write_switch(123, {"reason": "limit"})
        self.assertTrue(self.store.has_switch(123))
        self.assertEqual(self.store.take_switch(123), {"reason": "limit"})
        self.assertFalse(self.store.has_switch(123))

    def test_corrupt_switch_request_is_discarded(self):
        with open(self.store.switch_path(7), "w") as f:
            f.write("nope")
        self.assertIsNone(self.store.take_switch(7))
        self.assertFalse(self.store.has_switch(7))

    def test_kill_markers_and_clearing(self):
        self.assertFalse(self.store.take_killed(5))
        self.store.mark_killed(5)
        self.assertTrue(self.store.take_killed(5))
        self.assertFalse(self.store.take_killed(5))
        self.store.mark_killed(5)
        self.store.write_switch(5, {"reason": "limit"})
        self.store.clear_pending(5)
        self.assertFalse(self.store.take_killed(5))
        self.assertFalse(self.store.has_switch(5))

    def test_sessions_default_and_old_files_without_them(self):
        with open(self.store.state_path, "w") as f:
            json.dump({"accounts": {}, "launchers": {}}, f)
        self.assertEqual(self.store.read()["sessions"], {})

    def test_old_session_records_are_pruned(self):
        import time
        with self.store.locked() as st:
            st["sessions"]["old"] = {"account": "a", "at": time.time() - 15 * 86400}
            st["sessions"]["new"] = {"account": "a", "at": time.time()}
        self.assertEqual(list(self.store.read()["sessions"]), ["new"])

    def test_log_appends_lines(self):
        self.store.log("one")
        self.store.log("two")
        with open(self.store.log_path) as f:
            lines = f.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[1].endswith(" two"))

    def test_pid_alive(self):
        self.assertTrue(pid_alive(os.getpid()))

    def test_state_dir_from_env(self):
        with mock.patch.dict(os.environ, {"RCLAUDE_STATE_DIR": self.dir}):
            self.assertEqual(StateStore().dir, os.path.abspath(self.dir))
