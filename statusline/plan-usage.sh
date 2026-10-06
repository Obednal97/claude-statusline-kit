#!/usr/bin/env bash
# 5-hour and weekly plan usage for Pro/Max accounts, e.g. "5h: 62% ↻2h10m | 7d: 41% ↻Thu".
#
# Shows only when the active account is a personal Pro or Max plan
# (oauthAccount.organizationType in the active .claude.json). Team, Enterprise
# and API accounts print nothing, so the widget hides itself.
#
# Usage comes from Claude Code's own rate_limits on stdin — no network calls,
# no token access. Claude Code sends it only after the session's first API
# response, so the widget is blank until then.
#
# Colour: green under 50%, yellow under 80%, red from 80% (per window).
#
# Input: Claude Code's status JSON on stdin (rate_limits).

export CONFIG="${CLAUDE_CONFIG_DIR:+$CLAUDE_CONFIG_DIR/.claude.json}"
CONFIG="${CONFIG:-$HOME/.claude.json}"
NODE="$(command -v node)"
[ -z "$NODE" ] || [ ! -f "$CONFIG" ] && exit 0

"$NODE" -e '
const fs = require("fs");
let acct = {};
try { acct = JSON.parse(fs.readFileSync(process.env.CONFIG, "utf8")).oauthAccount || {}; } catch (e) {}
const PERSONAL_PLANS = ["claude_max", "claude_pro"];
if (!PERSONAL_PLANS.includes(acct.organizationType)) process.exit(0);

let d = {};
try { d = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) {}
const rl = d.rate_limits || {};

const now = Date.now() / 1000;
const colour = p => p >= 80 ? "\x1b[31m" : p >= 50 ? "\x1b[33m" : "\x1b[32m";
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
