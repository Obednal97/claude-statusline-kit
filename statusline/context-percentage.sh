#!/usr/bin/env bash
# Correct context-window % for Claude Code, including 1M-context models.
#
# ccstatusline's built-in context widget scales to 200k, so 1M-context models
# (Opus 4.6+, Sonnet 4.6+, the Claude 5 family incl. Fable 5) read past 100%.
# This resolves the real window per model.
#
# Window resolution order:
#   1. LiteLLM's public model DB (max_input_tokens) — cached locally, refreshed
#      in the background ~daily. Same community source ccusage uses for pricing,
#      so windows self-update as new models ship (no reinstall). The fetch NEVER
#      blocks a render: until the cache exists, the offline fallback is used.
#   2. Offline fallback: a [1m] suffix => 1M; a built-in list of 1M-default
#      models => 1M; everything else => 200k.
#
# Set USE_LIVE_WINDOWS=0 below to disable all network access (offline list only).
#
# Input: Claude Code's status JSON on stdin (model.id + transcript_path).
# Output: "Ctx: NN.N% (NNNk)"

USE_LIVE_WINDOWS=1
LITELLM_URL="https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
CACHE="/tmp/ccstatusline-model-windows-$(id -u).json"
LOCK="/tmp/ccstatusline-model-windows-$(id -u).lock"
MAX_AGE=86400   # refresh the window cache at most once per day

NODE="$(command -v node)"
if [ -z "$NODE" ]; then printf 'Ctx: ?'; exit 0; fi

# Refresh the cached window map from LiteLLM. Fully detached (own fds) so it
# never holds the status line's output pipe open — renders stay instant.
refresh_windows() {
  [ -f "$LOCK" ] && return
  command -v curl >/dev/null 2>&1 || return
  touch "$LOCK"
  curl -fsSL --max-time 6 "$LITELLM_URL" 2>/dev/null | "$NODE" -e '
    let s = ""; process.stdin.on("data", d => s += d).on("end", () => {
      let o = {}; try { o = JSON.parse(s); } catch (e) { process.exit(0); }
      const out = {};
      for (const k in o) { const m = o[k]; if (m && m.max_input_tokens) out[k] = m.max_input_tokens; }
      if (Object.keys(out).length) process.stdout.write(JSON.stringify(out));
    });
  ' > "$CACHE.tmp" 2>/dev/null
  if [ -s "$CACHE.tmp" ]; then mv "$CACHE.tmp" "$CACHE"; else rm -f "$CACHE.tmp"; fi
  rm -f "$LOCK"
}

if [ "$USE_LIVE_WINDOWS" = "1" ]; then
  STALE=0
  if [ ! -f "$CACHE" ]; then
    STALE=1
  else
    CT=$(stat -c %Y "$CACHE" 2>/dev/null || stat -f %m "$CACHE" 2>/dev/null)
    [ $(( $(date +%s) - ${CT:-0} )) -ge "$MAX_AGE" ] && STALE=1
  fi
  # detach fds so the background refresh can never stall the render
  [ "$STALE" = "1" ] && ( refresh_windows </dev/null >/dev/null 2>&1 & )
fi

CACHE="$CACHE" USE_LIVE="$USE_LIVE_WINDOWS" "$NODE" -e '
const fs = require("fs");
let data = {};
try { data = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) {}
const modelId = (data && data.model && data.model.id ? String(data.model.id) : "").toLowerCase();
const is1m = /\[1m\]/.test(modelId);

let windowSize = 0;

// 1. cached LiteLLM window map
if (process.env.USE_LIVE === "1") {
  try {
    const map = JSON.parse(fs.readFileSync(process.env.CACHE, "utf8"));
    const base = modelId.replace(/\[.*$/, "");   // strip a [1m]-style suffix
    windowSize = map[modelId] || map[base] || 0;
  } catch (e) {}
}

// 2. offline fallback
if (!windowSize) {
  const ONE_MILLION = ["claude-fable-5","claude-mythos-5","claude-opus-4-6","claude-opus-4-7","claude-opus-4-8","claude-sonnet-4-6","claude-sonnet-5"];
  windowSize = ONE_MILLION.some(m => modelId.indexOf(m) !== -1) ? 1000000 : 200000;
}
// a [1m] suffix always means the 1M beta, whatever the base entry says
if (is1m) windowSize = 1000000;

// current context tokens from the transcript (last usage record)
let ctx = 0;
const tpath = data && data.transcript_path;
if (tpath) {
  try {
    const lines = fs.readFileSync(tpath, "utf8").trim().split("\n");
    for (let i = lines.length - 1; i >= 0; i--) {
      let o; try { o = JSON.parse(lines[i]); } catch (e) { continue; }
      const u = o && o.message && o.message.usage;
      if (u && (u.input_tokens != null || u.cache_read_input_tokens != null)) {
        ctx = (u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0);
        break;
      }
    }
  } catch (e) {}
}

const pct = windowSize ? (ctx / windowSize * 100) : 0;
const ktok = ctx >= 1000 ? Math.round(ctx / 1000) + "k" : String(ctx);
process.stdout.write("Ctx: " + pct.toFixed(1) + "% (" + ktok + ")");
'
