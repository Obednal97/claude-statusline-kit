#!/usr/bin/env bash
# What the local model server is doing, for the money row in local mode,
# e.g. "🧠 100% GPU · 18 GB · 64K ctx".
#
# Only in local mode (local-mode.sh) on an Ollama endpoint; prints nothing
# otherwise. Reads the summary local-mode.sh caches from Ollama /api/ps (a
# detached background refresh, so the first render of a session shows nothing):
#   🧠 100% GPU · 18 GB · 64K ctx   loaded; GPU share of the model, memory, context length
#   🧠 62% GPU · ...                partly offloaded to the CPU (yellow): expect it to be slow
#   🧠 CPU · ...                    entirely on the CPU (red)
#   🧠 not loaded                   the next prompt waits for the model to load
#   🧠 Ollama not responding        (red)
#
# Input: none (environment only).

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

. "${BASH_SOURCE[0]%/*}/local-mode.sh" 2>/dev/null || exit 0
is_local_mode && local_mode_ollama || exit 0

export SERVER_JSON
SERVER_JSON="$(local_server_json)"
[ -n "$SERVER_JSON" ] || exit 0

NODE="$(command -v node)"
[ -z "$NODE" ] && exit 0

"$NODE" -e '
let s = {};
try { s = JSON.parse(process.env.SERVER_JSON); } catch (e) {}
const CYAN = "\x1b[36m", GREEN = "\x1b[32m", YELLOW = "\x1b[33m", RED = "\x1b[31m", DIM = "\x1b[2m", RESET = "\x1b[0m";
let out = "";
if (s.state === "loaded") {
  const share = s.size > 0 ? Math.round(Math.min(s.vram / s.size, 1) * 100) : 0;
  const where = s.vram <= 0 ? RED + "CPU" : share >= 100 ? GREEN + "100% GPU" : YELLOW + share + "% GPU";
  const parts = [where + RESET + CYAN];
  // GB as ollama ps shows them (powers of 1000)
  if (s.size > 0) { const gb = s.size / 1e9; parts.push((gb < 10 ? gb.toFixed(1) : Math.round(gb)) + " GB"); }
  if (s.ctx > 0) parts.push((s.ctx >= 1024 ? Math.round(s.ctx / 1024) + "K" : s.ctx) + " ctx");
  out = CYAN + "🧠 " + parts.join(" · ") + RESET;
} else if (s.state === "idle") {
  out = DIM + "🧠 not loaded" + RESET;
} else if (s.state === "down") {
  out = RED + "🧠 Ollama not responding" + RESET;
}
process.stdout.write(out);
'
