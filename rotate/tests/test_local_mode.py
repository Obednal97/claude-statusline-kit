"""Local-model mode (statusline/local-mode.sh): claude-local and other wrappers
that point ANTHROPIC_BASE_URL at Ollama or another non-Anthropic server."""
import glob
import http.server
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest

KIT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WIDGETS = os.path.join(KIT, "statusline")

# What the claude-local wrapper sets.
CLAUDE_LOCAL = {
    "ANTHROPIC_BASE_URL": "http://localhost:11434",
    "ANTHROPIC_AUTH_TOKEN": "ollama",
    "ANTHROPIC_MODEL": "qwen3.8:27b-mxfp8",
    "CLAUDE_CODE_MAX_CONTEXT_TOKENS": "65536",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}
# Settings that must not change anything: Anthropic itself, or local mode switched off.
NOT_LOCAL = [
    {},
    {"ANTHROPIC_BASE_URL": ""},
    {"ANTHROPIC_BASE_URL": "https://api.anthropic.com"},
    {"ANTHROPIC_BASE_URL": "https://API.Anthropic.com:443/v1/"},
    dict(CLAUDE_LOCAL, STATUSLINE_LOCAL_MODE="0"),
]
PAYLOAD = {
    "session_id": "s1",
    "model": {"id": "qwen3.8:27b-mxfp8", "display_name": "qwen3.8:27b-mxfp8"},
    "cost": {"total_cost_usd": 1.234},
    # Claude Code assumes a Claude-sized window; local mode must ignore it.
    "context_window": {"context_window_size": 200000,
                       "current_usage": {"input_tokens": 20000, "cache_read_input_tokens": 1000,
                                         "cache_creation_input_tokens": 0}},
    # No resets_at: the reset countdown would make renders depend on the clock.
    "rate_limits": {"five_hour": {"used_percentage": 62}, "seven_day": {"used_percentage": 41}},
    "workspace": {"current_dir": tempfile.gettempdir()},
}
CLAUDE_PAYLOAD = dict(PAYLOAD, model={"id": "claude-opus-5-5", "display_name": "Opus 5.5"},
                      cost={"total_cost_usd": 6.5})


# Local-mode widgets keep caches in /tmp (ccstatusline-local-server-*, ccstatusline-local-speed-*),
# and rendering with CLAUDE_LOCAL starts a background check of localhost:11434. Remove what the tests add.
CACHES = "/tmp/ccstatusline-local-*"
_caches_before = set()


def setUpModule():
    _caches_before.update(glob.glob(CACHES))


def tearDownModule():
    time.sleep(0.5)   # let background checks finish
    for f in set(glob.glob(CACHES)) - _caches_before:
        shutil.rmtree(f, ignore_errors=True) if os.path.isdir(f) else os.remove(f)


def base_env(extra):
    profile = tempfile.mkdtemp()
    with open(os.path.join(profile, ".claude.json"), "w") as f:
        json.dump({"oauthAccount": {"emailAddress": "you@example.com", "organizationType": "claude_max"}}, f)
    env = {"PATH": os.environ["PATH"], "HOME": tempfile.mkdtemp(), "CLAUDE_CONFIG_DIR": profile}
    env.update(extra)
    return env


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class DetectionTest(unittest.TestCase):
    def is_local(self, url, **extra):
        env = {"PATH": os.environ["PATH"]}
        if url is not None:
            env["ANTHROPIC_BASE_URL"] = url
        env.update(extra)
        p = subprocess.run(["bash", "-c", '. "$1" && is_local_mode && echo yes || echo no', "_",
                            os.path.join(WIDGETS, "local-mode.sh")], env=env, capture_output=True, text=True)
        return p.stdout.strip() == "yes"

    def test_local_hosts(self):
        for url in ["http://localhost:11434", "http://LOCALHOST:11434/", "http://127.0.0.1:11434/v1",
                    "http://[::1]:11434", "http://mac-mini.local:11434", "localhost:11434",
                    "https://llm.example.com/anthropic", "http://user:pw@192.168.1.20:8080"]:
            self.assertTrue(self.is_local(url), url)

    def test_anthropic_or_unset_is_not_local(self):
        for url in [None, "", "https://api.anthropic.com", "https://api.anthropic.com/",
                    "https://API.ANTHROPIC.COM:443/v1", "https://api.anthropic.com."]:
            self.assertFalse(self.is_local(url), url)

    def test_lookalike_hosts_are_not_anthropic(self):
        self.assertTrue(self.is_local("https://anthropic.com.example.net"))
        self.assertTrue(self.is_local("https://notanthropic.com"))

    def test_override(self):
        self.assertFalse(self.is_local("http://localhost:11434", STATUSLINE_LOCAL_MODE="0"))
        self.assertTrue(self.is_local(None, STATUSLINE_LOCAL_MODE="1"))


@unittest.skipUnless(shutil.which("node") and shutil.which("bash"), "needs node and bash")
class LocalWidgetTest(unittest.TestCase):
    def run_widget(self, name, env_extra, stdin=PAYLOAD):
        p = subprocess.run(["bash", os.path.join(WIDGETS, name)], env=base_env(env_extra),
                           input=json.dumps(stdin), capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_model_shows_local_short_name(self):
        self.assertEqual(self.run_widget("model.sh", CLAUDE_LOCAL), "Model: Local · qwen3.8 27B")
        cases = {"llama3.1:8b-instruct-q4_K_M": "llama3.1 8B", "gpt-oss:20b": "gpt-oss 20B",
                 "deepseek-r1:latest": "deepseek-r1", "mistral": "mistral",
                 "hf.co/someone/Some-Model-GGUF:Q4_K_M": "Some-Model-GGUF", "qwen3:0.6b": "qwen3 0.6B"}
        for tag, short in cases.items():
            out = self.run_widget("model.sh", dict(CLAUDE_LOCAL, ANTHROPIC_MODEL=tag))
            self.assertEqual(out, "Model: Local · " + short, tag)

    def test_model_falls_back_to_the_stdin_model_id(self):
        env = dict(CLAUDE_LOCAL)
        del env["ANTHROPIC_MODEL"]
        self.assertEqual(self.run_widget("model.sh", env), "Model: Local · qwen3.8 27B")

    def test_context_uses_max_context_tokens(self):
        # 21k of 65536, not of the 200k Claude Code reported
        self.assertEqual(self.run_widget("context-percentage.sh", CLAUDE_LOCAL), "Ctx: 32.0% (21k)")

    def test_context_token_count_stays_live(self):
        stdin = dict(PAYLOAD, context_window={"context_window_size": 200000,
                                              "current_usage": {"input_tokens": 49152}})
        self.assertEqual(self.run_widget("context-percentage.sh", CLAUDE_LOCAL, stdin), "Ctx: 75.0% (49k)")

    def test_context_default_window_without_max_context_tokens(self):
        env = dict(CLAUDE_LOCAL, ANTHROPIC_MODEL="no-such-model-for-tests:7b")
        del env["CLAUDE_CODE_MAX_CONTEXT_TOKENS"]
        self.assertEqual(self.run_widget("context-percentage.sh", env), "Ctx: 64.1% (21k)")   # of 32768

    def test_costs_hidden(self):
        for name in ["session-cost.sh", "daily-cost.sh", "weekly-cost.sh", "monthly-cost.sh"]:
            self.assertEqual(self.run_widget(name, CLAUDE_LOCAL), "", name)

    def test_plan_usage_hidden(self):
        self.assertEqual(self.run_widget("plan-usage.sh", CLAUDE_LOCAL), "")
        self.assertEqual(self.run_widget("plan-usage.sh", dict(CLAUDE_LOCAL, RCLAUDE_AUTH="token")), "")

    def test_account_never_claims_an_account(self):
        out = self.run_widget("account.sh", CLAUDE_LOCAL)
        self.assertEqual(out, "🖥 Local model (Ollama) · not using your Claude account")
        out = self.run_widget("account.sh", {"ANTHROPIC_BASE_URL": "http://192.168.1.20:8080",
                                             "RCLAUDE_AUTH": "token", "RCLAUDE_ACCOUNT_EMAIL": "you.too@example.com"})
        self.assertEqual(out, "🖥 Local model · not using your Claude account")

    def test_without_local_mode_output_is_unchanged(self):
        expected = {
            "model.sh": "Model: Opus 5.5",
            "session-cost.sh": "Cost: $6.50",
            "context-percentage.sh": "Ctx: 10.5% (21k)",
            "account.sh": "👤 you@example.com",
        }
        for env in NOT_LOCAL:
            for name, want in expected.items():
                self.assertEqual(self.run_widget(name, env, CLAUDE_PAYLOAD), want, (name, env))
            self.assertIn("5h: 62%", self.run_widget("plan-usage.sh", env, CLAUDE_PAYLOAD))
            for name in ["daily-cost.sh", "weekly-cost.sh", "monthly-cost.sh", "model-tags.sh"]:
                self.assertEqual(self.run_widget(name, env, CLAUDE_PAYLOAD),
                                 self.run_widget(name, {}, CLAUDE_PAYLOAD), (name, env))
            for name in ["local-speed.sh", "local-server.sh"]:
                self.assertEqual(self.run_widget(name, env, CLAUDE_PAYLOAD), "", (name, env))

    def test_model_and_cost_scripts_match_the_built_in_widgets(self):
        # ccstatusline: "Model: " + (display_name ?? id) minus a trailing " (...)"; nothing without a model.
        cases = [({"model": {"id": "x", "display_name": "Sonnet 4.5 (with 1M context)"}}, "Model: Sonnet 4.5"),
                 ({"model": {"id": "claude-haiku-4-5"}}, "Model: claude-haiku-4-5"),
                 ({"model": "Opus"}, "Model: Opus"), ({}, "")]
        for stdin, want in cases:
            self.assertEqual(self.run_widget("model.sh", {}, stdin), want, stdin)
        # "Cost: $" + toFixed(2); nothing without a cost.
        for cost, want in [(0, "Cost: $0.00"), (0.004, "Cost: $0.00"), (12.3456, "Cost: $12.35")]:
            self.assertEqual(self.run_widget("session-cost.sh", {}, {"cost": {"total_cost_usd": cost}}), want)
        self.assertEqual(self.run_widget("session-cost.sh", {}, {}), "")


def entry(kind, ts, **message):
    e = {"type": kind, "timestamp": "2026-10-22T10:%s.000Z" % ts}
    if message:
        e["message"] = message
    return json.dumps(e) + "\n"


@unittest.skipUnless(shutil.which("node") and shutil.which("bash"), "needs node and bash")
class LocalSpeedTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.transcript = os.path.join(self.dir, "session.jsonl")
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, *lines):
        with open(self.transcript, "a") as f:
            f.writelines(lines)

    def speed(self, env=CLAUDE_LOCAL):
        p = subprocess.run(["bash", os.path.join(WIDGETS, "local-speed.sh")], env=base_env(env),
                           input=json.dumps({"transcript_path": self.transcript}),
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def test_session_average_and_last_reply(self):
        self.write(entry("user", "00:00"),
                   # one reply in two content blocks, 0s to 10s, 200 tokens
                   entry("assistant", "00:05", id="m1", usage={"output_tokens": 200}),
                   entry("assistant", "00:10", id="m1", usage={"output_tokens": 200}),
                   entry("user", "00:11"),
                   entry("api-request", "00:12"),
                   # 12s to 23s, 412 tokens
                   entry("assistant", "00:23", id="m2", usage={"output_tokens": 412}))
        self.assertEqual(self.speed(), "⚡ 29 tok/s · last 412 tok in 11s")   # 612 tok / 21s
        # new lines are picked up on the next render
        self.write(entry("user", "01:00"), entry("assistant", "01:02", id="m3", usage={"output_tokens": 5}))
        self.assertEqual(self.speed(), "⚡ 27 tok/s · last 5 tok in 2.0s")    # 617 tok / 23s

    def test_long_replies_and_slow_rates(self):
        self.write(entry("user", "00:00"), entry("assistant", "01:30", id="m1", usage={"output_tokens": 1234}))
        self.assertEqual(self.speed(), "⚡ 14 tok/s · last 1.2k tok in 1m 30s")
        self.write(entry("user", "02:00"), entry("assistant", "04:00", id="m2", usage={"output_tokens": 66}))
        self.assertEqual(self.speed(), "⚡ 6.2 tok/s · last 66 tok in 2m 0s")   # 1300 tok / 210s

    def test_subagents_and_half_written_lines_are_skipped(self):
        side = json.loads(entry("assistant", "00:02", id="s1", usage={"output_tokens": 9999}))
        side["isSidechain"] = True
        self.write(entry("user", "00:00"), json.dumps(side) + "\n",
                   entry("assistant", "00:04", id="m1", usage={"output_tokens": 40}),
                   '{"type":"assistant","timestamp":"2026-10-22T10:00:09.000Z","mess')
        self.assertEqual(self.speed(), "⚡ 10 tok/s · last 40 tok in 4.0s")

    def test_nothing_before_the_first_reply_or_outside_local_mode(self):
        self.write(entry("user", "00:00"))
        self.assertEqual(self.speed(), "")
        self.write(entry("assistant", "00:04", id="m1", usage={"output_tokens": 40}))
        self.assertEqual(self.speed({}), "")


class FakeOllama:
    """Answers GET /api/ps like Ollama, with whatever self.models holds."""

    def __init__(self, models):
        self.models = models
        fake = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"models": fake.models}).encode()
                self.send_response(200 if self.path == "/api/ps" else 404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


QWEN = {"name": "qwen3.8:27b-mxfp8", "model": "qwen3.8:27b-mxfp8", "size": 18_400_000_000,
        "size_vram": 18_400_000_000, "context_length": 65536}


@unittest.skipUnless(shutil.which("node") and shutil.which("bash") and shutil.which("curl"),
                     "needs node, bash and curl")
class LocalServerTest(unittest.TestCase):
    def setUp(self):
        self.ollama = FakeOllama([QWEN])
        self.addCleanup(self.ollama.close)
        self.env = dict(CLAUDE_LOCAL, ANTHROPIC_BASE_URL=self.ollama.url)
        self.addCleanup(self.remove_cache)

    def cache(self):
        p = subprocess.run(["bash", "-c", '. "$1" && local_server_cache', "_", os.path.join(WIDGETS, "local-mode.sh")],
                           env=dict(os.environ, **self.env), capture_output=True, text=True)
        return p.stdout

    def remove_cache(self):
        path = self.cache()
        for f in [path, path + ".tmp"]:
            if os.path.exists(f):
                os.remove(f)
        shutil.rmtree(path + ".lockd", ignore_errors=True)

    def widget(self, name="local-server.sh", stdin=PAYLOAD):
        p = subprocess.run(["bash", os.path.join(WIDGETS, name)], env=base_env(self.env),
                           input=json.dumps(stdin), capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def refreshed(self, name="local-server.sh"):
        """Render once to start the background refresh, wait for it, render again."""
        self.remove_cache()
        first = self.widget(name)
        if name == "local-server.sh":
            self.assertEqual(first, "")   # nothing until the refresh lands
        deadline = time.time() + 10
        while not os.path.exists(self.cache()) and time.time() < deadline:
            time.sleep(0.05)
        self.assertTrue(os.path.exists(self.cache()), "background refresh never wrote the cache")
        return self.widget(name)

    def plain(self, out):
        return re.sub("\x1b\\[[0-9;]*m", "", out)

    def test_loaded_on_gpu(self):
        out = self.refreshed()
        self.assertEqual(self.plain(out), "🧠 100% GPU · 18 GB · 64K ctx")
        self.assertIn("\x1b[32m100% GPU", out)   # green

    def test_partly_and_fully_on_cpu(self):
        self.ollama.models = [dict(QWEN, size_vram=11_408_000_000)]
        out = self.refreshed()
        self.assertEqual(self.plain(out), "🧠 62% GPU · 18 GB · 64K ctx")
        self.assertIn("\x1b[33m62% GPU", out)    # yellow
        self.ollama.models = [dict(QWEN, size_vram=0, size=4_700_000_000, context_length=0)]
        self.assertEqual(self.plain(self.refreshed()), "🧠 CPU · 4.7 GB")

    def test_not_loaded_and_latest_tag(self):
        self.ollama.models = [dict(QWEN, name="llama3.1:8b", model="llama3.1:8b")]
        self.assertEqual(self.plain(self.refreshed()), "🧠 not loaded")
        self.env["ANTHROPIC_MODEL"] = "mistral"
        self.ollama.models = [dict(QWEN, name="mistral:latest", model="mistral:latest")]
        self.assertEqual(self.plain(self.refreshed()), "🧠 100% GPU · 18 GB · 64K ctx")

    def test_server_down(self):
        self.ollama.close()
        self.assertEqual(self.plain(self.refreshed()), "🧠 Ollama not responding")

    def test_context_uses_the_served_window(self):
        self.ollama.models = [dict(QWEN, context_length=32768)]
        # the served 32K wins over CLAUDE_CODE_MAX_CONTEXT_TOKENS=65536
        self.assertEqual(self.refreshed("context-percentage.sh"), "Ctx: 64.1% (21k)")

    def test_only_for_ollama_endpoints(self):
        self.env.pop("ANTHROPIC_AUTH_TOKEN")   # a non-11434 port without the ollama token
        self.assertEqual(self.widget(), "")
        time.sleep(0.3)
        self.assertFalse(os.path.exists(self.cache()))


@unittest.skipUnless(shutil.which("node") and shutil.which("bash") and shutil.which("ccstatusline"),
                     "needs node, bash and ccstatusline")
class PresetRenderTest(unittest.TestCase):
    """The whole preset, rendered by ccstatusline: outside local mode it must be byte-for-byte
    what the preset with ccstatusline's built-in model and session-cost widgets renders."""

    def render(self, settings, payload, env_extra):
        env = base_env(env_extra)
        cfg = os.path.join(env["HOME"], ".config", "ccstatusline")
        os.makedirs(cfg)
        for f in os.listdir(WIDGETS):
            shutil.copy(os.path.join(WIDGETS, f), cfg)
        with open(os.path.join(cfg, "settings.json"), "w") as f:
            json.dump(settings, f)
        p = subprocess.run(["ccstatusline"], env=env, input=json.dumps(payload), cwd=tempfile.gettempdir(),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p.stdout

    def presets(self):
        """The kit preset, and the same preset as it was before local mode: built-in model and
        session-cost widgets, and no local-speed/local-server widgets (or their separators)."""
        with open(os.path.join(KIT, "settings.json")) as f:
            new = json.load(f)
        old = json.loads(json.dumps(new))
        builtin = {"model.sh": "model", "session-cost.sh": "session-cost"}
        local_only = {"local-speed.sh", "local-server.sh"}
        for i, line in enumerate(old["lines"]):
            kept = []
            for w in line:
                name = os.path.basename(w.get("commandPath", ""))
                if name in local_only:
                    if kept and kept[-1]["type"] == "separator":
                        kept.pop()
                    continue
                if name in builtin:
                    w["type"] = builtin[name]
                    del w["commandPath"], w["timeout"]
                kept.append(w)
            old["lines"][i] = kept
        return old, new

    def test_unchanged_outside_local_mode(self):
        old, new = self.presets()
        payloads = [CLAUDE_PAYLOAD, dict(CLAUDE_PAYLOAD, model={"id": "claude-sonnet-4-5",
                                                                "display_name": "Sonnet 4.5 (with 1M context)"}),
                    {"session_id": "s2", "workspace": {"current_dir": tempfile.gettempdir()}}]
        for payload in payloads:
            for env in NOT_LOCAL:
                self.assertEqual(self.render(new, payload, env), self.render(old, payload, env), env)

    def test_local_render(self):
        _, new = self.presets()
        out = self.render(new, PAYLOAD, CLAUDE_LOCAL).replace(" ", " ")
        for gone in ["Cost:", "Day:", "Wk:", "Mo:", "5h:"]:
            self.assertNotIn(gone, out)
        self.assertIn("Model: Local · qwen3.8 27B", out)
        self.assertIn("Ctx: 32.0% (21k)", out)
        self.assertIn("🖥 Local model (Ollama) · not using your Claude account", out)
        self.assertNotIn("5h:", out)
        self.assertNotIn("you@example.com", out)
