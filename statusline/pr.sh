#!/usr/bin/env bash
# The branch's open pull/merge request for the git row, e.g. "PR #42 ✓ approved".
#
# Reads Claude Code's own `pr` field (sent since Claude Code 2.1.3xx, found by
# Claude Code itself from the branch's upstream), so no gh/glab CLI or network
# call is needed and the PR is there on the first render:
#   PR #42                       open, no review decision yet (or "pending")
#   PR #42 ✓ approved            green
#   PR #42 ✗ changes requested   red
#   PR #42 · draft               dim
#   MR !7 …                      a GitLab merge request (kind "mr")
# The number is an OSC 8 link to the PR, like the branch widget. Prints nothing
# when Claude Code sends no `pr` (no PR for the branch, or an older Claude
# Code), so the widget and its separator hide themselves.
#
# Colour: the git row's brightBlack, set here because the widget has
# preserveColors (ccstatusline would otherwise strip the link escapes).
#
# Input: Claude Code's status JSON on stdin (pr.number, pr.url, pr.review_state, pr.kind).

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

NODE="$(command -v node)"
[ -z "$NODE" ] && exit 0

"$NODE" -e '
const fs = require("fs");
let d = {};
try { d = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) {}
const pr = d && d.pr;
const n = pr && Number(pr.number);
if (!(n > 0)) process.exit(0);

const BASE = "\x1b[90m", GREEN = "\x1b[32m", RED = "\x1b[31m", DIM = "\x1b[2m", RESET = "\x1b[0m";
const label = (pr.kind === "mr" ? "MR !" : "PR #") + n;
const url = typeof pr.url === "string" && /^https?:\/\//.test(pr.url) ? pr.url : "";
// OSC 8 hyperlink around the number; terminals without support show plain text
const link = url ? "\x1b]8;;" + url + "\x1b\\" + label + "\x1b]8;;\x1b\\" : label;

const STATES = {
  approved: GREEN + " ✓ approved",
  changes_requested: RED + " ✗ changes requested",
  draft: DIM + " · draft"
};
const state = STATES[String(pr.review_state || "")] || "";
process.stdout.write(BASE + link + state + RESET);
'
