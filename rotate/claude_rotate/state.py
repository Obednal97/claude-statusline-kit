"""Locked JSON state shared by every rclaude process, plus switch-request files and the log."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time
from typing import Iterator, Optional

DEFAULT_STATE_DIR = "~/.local/state/claude-rotate"
SESSION_TTL_SECONDS = 14 * 86400


def default_state() -> dict:
    # sessions: session_id -> {"account", "at"}: which account rclaude last saw each conversation on
    return {"accounts": {}, "launchers": {}, "sessions": {}}


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class StateStore:
    def __init__(self, state_dir: Optional[str] = None):
        d = state_dir or os.environ.get("RCLAUDE_STATE_DIR") or DEFAULT_STATE_DIR
        self.dir = os.path.abspath(os.path.expanduser(d))
        os.makedirs(self.dir, mode=0o700, exist_ok=True)
        self.state_path = os.path.join(self.dir, "state.json")
        self.lock_path = os.path.join(self.dir, "state.lock")
        self.log_path = os.path.join(self.dir, "rclaude.log")
        self.settings_path = os.path.join(self.dir, "rclaude-settings.json")

    @contextlib.contextmanager
    def _lock(self) -> Iterator[None]:
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _load(self) -> dict:
        try:
            with open(self.state_path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return default_state()
        except (ValueError, OSError):
            data = None
        if (not isinstance(data, dict) or not isinstance(data.get("accounts", {}), dict)
                or not isinstance(data.get("launchers", {}), dict)):
            dest = "%s.corrupt-%d" % (self.state_path, int(time.time()))
            with contextlib.suppress(OSError):
                os.replace(self.state_path, dest)
            self.log("state file was unreadable; moved to %s and started fresh" % dest)
            return default_state()
        data.setdefault("accounts", {})
        data.setdefault("launchers", {})
        if not isinstance(data.get("sessions"), dict):
            data["sessions"] = {}
        return data

    def _save(self, data: dict) -> None:
        data["launchers"] = {k: v for k, v in data["launchers"].items() if k.isdigit() and pid_alive(int(k))}
        cutoff = time.time() - SESSION_TTL_SECONDS
        data["sessions"] = {k: v for k, v in data.get("sessions", {}).items()
                            if isinstance(v, dict) and float(v.get("at", 0) or 0) >= cutoff}
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".state-", suffix=".json")  # created 0600
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp, self.state_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    @contextlib.contextmanager
    def locked(self) -> Iterator[dict]:
        """Exclusive read-modify-write. Changes are saved when the block exits normally."""
        with self._lock():
            data = self._load()
            yield data
            self._save(data)

    def read(self) -> dict:
        with self._lock():
            return self._load()

    def log(self, message: str) -> None:
        line = "%s %d %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), message)
        with contextlib.suppress(OSError):
            with open(self.log_path, "a") as f:
                f.write(line)

    def switch_path(self, launcher_pid: int) -> str:
        return os.path.join(self.dir, "switch-%d.json" % launcher_pid)

    def has_switch(self, launcher_pid: int) -> bool:
        return os.path.exists(self.switch_path(launcher_pid))

    def write_switch(self, launcher_pid: int, request: dict) -> None:
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".switch-")
        with os.fdopen(fd, "w") as f:
            json.dump(request, f)
        os.replace(tmp, self.switch_path(launcher_pid))

    def killed_path(self, launcher_pid: int) -> str:
        return os.path.join(self.dir, "killed-%d" % launcher_pid)

    def mark_killed(self, launcher_pid: int) -> None:
        """Written just before rclaude's own SIGTERM; a switch request is only honoured if this exists."""
        with open(self.killed_path(launcher_pid), "w"):
            pass

    def take_killed(self, launcher_pid: int) -> bool:
        try:
            os.unlink(self.killed_path(launcher_pid))
        except FileNotFoundError:
            return False
        return True

    def clear_pending(self, launcher_pid: int) -> None:
        """Drop leftovers from an earlier launcher that had the same PID."""
        self.take_killed(launcher_pid)
        self.take_switch(launcher_pid)

    def take_switch(self, launcher_pid: int) -> Optional[dict]:
        path = self.switch_path(launcher_pid)
        try:
            with open(path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except ValueError:
            data = None
        with contextlib.suppress(OSError):
            os.unlink(path)
        return data if isinstance(data, dict) else None
