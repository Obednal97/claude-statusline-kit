#!/usr/bin/env bash
# Session cost for the money row, e.g. "Cost: $6.50".
#
# Normally prints exactly what ccstatusline's built-in session-cost widget
# prints: "Cost: $" + cost.total_cost_usd to 2 decimals, and nothing when
# Claude Code sends no cost.
#
# In local mode (local-mode.sh: ANTHROPIC_BASE_URL is not Anthropic, e.g.
# claude-local on Ollama) it prints "Cost: Local · $0": a local model is free,
# and the figure Claude Code sends would be priced as if it were Claude.
#
# Input: Claude Code's status JSON on stdin (cost.total_cost_usd).

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

. "${BASH_SOURCE[0]%/*}/local-mode.sh" 2>/dev/null || is_local_mode() { return 1; }
if is_local_mode; then
  printf 'Cost: Local · $0'
  exit 0
fi

NODE="$(command -v node)"
[ -z "$NODE" ] && exit 0

"$NODE" -e '
const fs = require("fs");
let d = {};
try { d = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) {}
const c = d && d.cost && d.cost.total_cost_usd;
if (typeof c === "number") process.stdout.write("Cost: $" + c.toFixed(2));
'
