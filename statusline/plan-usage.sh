#!/usr/bin/env bash
# 5-hour and weekly plan usage for Pro/Max accounts, e.g. "5h: 62% ↻2h10m | 7d: 41% ↻Thu".
#
# Shows only when the active account is a personal Pro or Max plan
# (oauthAccount.organizationType in the active .claude.json), or an rclaude
# token tier (RCLAUDE_AUTH=token, a personal plan by design). Team, Enterprise
# and API accounts print nothing, so the widget hides itself.
#
# Usage comes from Claude Code's own rate_limits on stdin — no network calls,
# no token access. Claude Code sends it only after the session's first API
# response, so the widget is blank until then.
#
# Colour: each window fades from green (0%) through yellow (50%) to red (100%).
# 24-bit colour when COLORTERM says the terminal supports it, else the nearest
# of the 256 xterm colours.
#
# Hidden in local mode (local-mode.sh, e.g. claude-local on Ollama): a local
# model has no plan limits.
#
# Input: Claude Code's status JSON on stdin (rate_limits).

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

. "${BASH_SOURCE[0]%/*}/local-mode.sh" 2>/dev/null || is_local_mode() { return 1; }
is_local_mode && exit 0

export CONFIG="${CLAUDE_CONFIG_DIR:+$CLAUDE_CONFIG_DIR/.claude.json}"
# Claude Code on Windows keeps .claude.json in %USERPROFILE%, whatever $HOME says.
HOME_DIR="$HOME"
case "${OSTYPE:-}" in msys*|cygwin*) HOME_DIR="${USERPROFILE:-$HOME}" ;; esac
CONFIG="${CONFIG:-$HOME_DIR/.claude.json}"
NODE="$(command -v node)"
[ -z "$NODE" ] || [ ! -f "$CONFIG" ] && exit 0

"$NODE" -e '
const fs = require("fs");
let acct = {};
try { acct = JSON.parse(fs.readFileSync(process.env.CONFIG, "utf8")).oauthAccount || {}; } catch (e) {}
const PERSONAL_PLANS = ["claude_max", "claude_pro"];
if (process.env.RCLAUDE_AUTH !== "token" && !PERSONAL_PLANS.includes(acct.organizationType)) process.exit(0);

let d = {};
try { d = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) {}
const rl = d.rate_limits || {};

const now = Date.now() / 1000;
// Green at 0%, yellow at 50%, red at 100%, blended in between.
const STOPS = [[63, 185, 80], [210, 153, 34], [248, 81, 73]];
const TRUECOLOR = /truecolor|24bit/i.test(process.env.COLORTERM || "");
function colour(p) {
  const t = Math.min(Math.max(p, 0), 100) / 50;
  const i = Math.min(Math.floor(t), 1), f = t - i;
  const [r, g, b] = STOPS[i].map((c, k) => Math.round(c + (STOPS[i + 1][k] - c) * f));
  if (TRUECOLOR) return "\x1b[38;2;" + r + ";" + g + ";" + b + "m";
  // Nearest colour in the xterm 256-colour cube (levels 0, 95, 135, 175, 215, 255).
  const q = c => c < 48 ? 0 : c < 115 ? 1 : Math.floor((c - 35) / 40);
  return "\x1b[38;5;" + (16 + 36 * q(r) + 6 * q(g) + q(b)) + "m";
}
const RESET = "\x1b[0m";
const DAYS = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];

function resetIn(at, long) {
  const s = at - now;
  if (!(s > 0)) return "";
  if (long && s >= 86400) return DAYS[new Date(at * 1000).getDay()];
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
  return h ? h + "h" + String(m).padStart(2, "0") + "m" : m + "m";
}

function seg(label, w, long) {
  if (!w || w.used_percentage == null) return "";
  const p = Math.round(w.used_percentage);
  const r = resetIn(w.resets_at, long);
  return colour(p) + label + ": " + p + "%" + (r ? " ↻" + r : "") + RESET;
}

const out = [seg("5h", rl.five_hour, false), seg("7d", rl.seven_day, true)].filter(Boolean);
process.stdout.write(out.join(" | "));
'
