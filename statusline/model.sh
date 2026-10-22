#!/usr/bin/env bash
# Model name for the model row, e.g. "Model: Opus 5.5".
#
# Normally prints exactly what ccstatusline's built-in model widget prints:
# "Model: " + model.display_name (else model.id), minus a trailing
# " (...)" note. Prints nothing when there is no model, like the built-in.
#
# In local mode (local-mode.sh: ANTHROPIC_BASE_URL is not Anthropic, e.g.
# claude-local on Ollama) it prints "Model: Local · <short name>", the short
# name coming from ANTHROPIC_MODEL: "qwen3.8:27b-mxfp8" -> "qwen3.8 27B".
#
# Input: Claude Code's status JSON on stdin.

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

. "${BASH_SOURCE[0]%/*}/local-mode.sh" 2>/dev/null || is_local_mode() { return 1; }
export LOCAL=0
is_local_mode && LOCAL=1

NODE="$(command -v node)"
[ -z "$NODE" ] && exit 0

"$NODE" -e '
const fs = require("fs");
let d = {};
try { d = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) {}
const m = d && d.model;
const name = typeof m === "string" ? m : (m && (m.display_name != null ? m.display_name : m.id));

if (process.env.LOCAL === "1") {
  // Ollama tags look like name:tag, e.g. qwen3.8:27b-mxfp8, llama3.1:8b-instruct-q4_K_M,
  // hf.co/org/model:Q4_K_M. Keep the base name and a parameter size such as 27B.
  let raw = String(process.env.ANTHROPIC_MODEL || (m && m.id) || name || "").trim();
  raw = raw.replace(/\[.*$/, "");
  const slash = raw.lastIndexOf("/");
  if (slash !== -1) raw = raw.slice(slash + 1);
  const colon = raw.indexOf(":");
  let base = colon === -1 ? raw : raw.slice(0, colon);
  const tag = colon === -1 ? "" : raw.slice(colon + 1);
  const size = (tag.split(/[-_]/)[0] || "").match(/^(\d+(?:\.\d+)?)([bmk])$/i);
  let short = base + (size ? " " + size[1] + size[2].toUpperCase() : "");
  process.stdout.write("Model: Local" + (short ? " · " + short : ""));
  process.exit(0);
}

// Same output as the built-in model widget.
if (name) process.stdout.write("Model: " + String(name).replace(/\s*\(.*\)$/, ""));
'
