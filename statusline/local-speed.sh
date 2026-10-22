#!/usr/bin/env bash
# Generation speed for the money row in local mode,
# e.g. "⚡ 38 tok/s · last 412 tok in 11s".
#
# Only in local mode (local-mode.sh); prints nothing otherwise, or before the
# first reply. Worked out from the session transcript: each reply (one API
# response) runs from the last entry before it (the request, prompt or tool
# result) to its last content block, and its output_tokens come from usage.
#   38 tok/s            all output tokens this session / time spent on replies
#   last 412 tok in 11s the latest reply
# The times include prompt processing, so on a long context this reads lower
# than the server's own generation rate: it is the speed you actually wait on.
#
# The transcript is read incrementally: totals and a byte offset are kept in a
# small state file per transcript, so each render only parses the new lines.
#
# Input: Claude Code's status JSON on stdin (transcript_path).

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

. "${BASH_SOURCE[0]%/*}/local-mode.sh" 2>/dev/null || exit 0
is_local_mode || exit 0

NODE="$(command -v node)"
[ -z "$NODE" ] && exit 0

STATE_DIR="$(local_native_path /tmp)" UID_TAG="$(id -u)" "$NODE" -e '
const fs = require("fs"), path = require("path"), crypto = require("crypto");
let d = {};
try { d = JSON.parse(fs.readFileSync(0, "utf8")); } catch (e) {}
const tpath = d && d.transcript_path;
if (!tpath) process.exit(0);

const hash = crypto.createHash("sha1").update(String(tpath)).digest("hex").slice(0, 12);
const stateFile = path.join(process.env.STATE_DIR, "ccstatusline-local-speed-" + process.env.UID_TAG + "-" + hash + ".json");
const fresh = () => ({ path: tpath, offset: 0, out: 0, ms: 0, lastStart: 0, cur: null });
let st;
try { st = JSON.parse(fs.readFileSync(stateFile, "utf8")); } catch (e) {}
let size = 0;
try { size = fs.statSync(tpath).size; } catch (e) { process.exit(0); }
if (!st || st.path !== tpath || st.offset > size) st = fresh();   // new, or rewritten transcript

// A finished reply joins the session totals; cur is the latest reply, which may still be growing.
function finish() {
  const c = st.cur;
  if (c && c.out > 0 && c.end > c.start) { st.out += c.out; st.ms += c.end - c.start; }
}

if (size > st.offset) {
  const fd = fs.openSync(tpath, "r");
  const buf = Buffer.alloc(size - st.offset);
  fs.readSync(fd, buf, 0, buf.length, st.offset);
  fs.closeSync(fd);
  const nl = buf.lastIndexOf(10);   // only whole lines; a half-written one waits for the next render
  if (nl !== -1) {
    for (const line of buf.toString("utf8", 0, nl).split("\n")) {
      let o; try { o = JSON.parse(line); } catch (e) { continue; }
      if (!o || o.isSidechain) continue;
      const ts = Date.parse(o.timestamp);
      if (!(ts > 0)) continue;
      const m = o.message;
      if (o.type === "assistant" && m && m.usage) {
        const id = m.id || o.requestId || o.uuid;
        const out = Number(m.usage.output_tokens) || 0;
        if (st.cur && st.cur.id === id) {
          st.cur.end = Math.max(st.cur.end, ts);
          st.cur.out = Math.max(st.cur.out, out);
        } else {
          finish();
          const start = Math.max(st.lastStart, st.cur ? st.cur.end : 0) || ts;
          st.cur = { id: id, start: start, end: ts, out: out };
        }
      } else if (o.type !== "assistant") {
        st.lastStart = Math.max(st.lastStart, ts);
      }
    }
    st.offset += nl + 1;
    try { fs.writeFileSync(stateFile + ".tmp", JSON.stringify(st)); fs.renameSync(stateFile + ".tmp", stateFile); } catch (e) {}
  }
}

const c = st.cur;
const curOk = c && c.out > 0 && c.end > c.start;
const out = st.out + (curOk ? c.out : 0), ms = st.ms + (curOk ? c.end - c.start : 0);
if (!(out > 0 && ms > 0)) process.exit(0);

const rate = n => n >= 10 ? String(Math.round(n)) : n.toFixed(1);
const toks = n => n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n);
function secs(t) {
  const s = t / 1000;
  if (s < 10) return s.toFixed(1) + "s";
  if (s < 60) return Math.round(s) + "s";
  const r = Math.round(s);
  return Math.floor(r / 60) + "m " + (r % 60) + "s";
}
let text = "⚡ " + rate(out / (ms / 1000)) + " tok/s";
if (curOk) text += " · last " + toks(c.out) + " tok in " + secs(c.end - c.start);
process.stdout.write(text);
'
