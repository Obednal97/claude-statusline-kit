#!/usr/bin/env bash
# Tags for the model row: reasoning effort and fast mode, e.g. " · xhigh ⚡fast".
#
# Reads Claude Code's live per-session values (effort.level, fast_mode), so a
# mid-session /effort or /fast change shows on the next render. Prints nothing
# when there is nothing to tag, so the widget (and its separator) hides itself.
#
# Input: Claude Code's status JSON on stdin.

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

NODE="$(command -v node)"
[ -z "$NODE" ] && exit 0

"$NODE" -e '
const fs = require("fs");
let d = {};
try { d = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) {}
const tags = [];
if (d.effort && d.effort.level) tags.push(String(d.effort.level));
if (d.fast_mode === true) tags.push("⚡fast");
// leading SGR keeps the space before "·" (ccstatusline trims widget output)
process.stdout.write(tags.length ? "\x1b[35m · " + tags.join(" ") + "\x1b[0m" : "\x1b[0m");  // never empty: an empty merged widget would swallow the next separator
'
