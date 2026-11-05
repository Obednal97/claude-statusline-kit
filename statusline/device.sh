#!/usr/bin/env bash
# Device row: CPU, memory and disk as bars, e.g. "CPU ██░░░░ 36% | Mem ████░░ 69% | Disk █████░ 81%".
#
# An early warning that the machine is under strain, not a process monitor.
# Each bar is green below 60%, yellow from 60% and red from 85%.
#   CPU   all cores, measured between renders (cumulative CPU times from Node,
#         cached in the temp folder). It also turns yellow when the 15-minute
#         load average is above 30% of the cores, so a moderate load that
#         never goes away (a forgotten emulator or VM) still stands out.
#   Mem   memory in use as the OS counts it for pressure: on macOS
#         100 - kern.memorystatus_level (memory_pressure "free percentage";
#         Activity Monitor "Memory Used" counts wired VM memory and cache and
#         sits near full on a Mac running Colima); on Linux MemTotal minus
#         MemAvailable; elsewhere Node os.freemem.
#   Disk  the volume holding your home folder. macOS: share of the APFS
#         container that is not free (the Data volume alone undercounts);
#         elsewhere df Use% (used / (used + available)).
#
# Windows has no load average, so CPU there is the current figure only.
# Shown in local mode too: a local model is what most often fills memory.
#
# Input: none (Claude Code status JSON on stdin is ignored).

# Windows (Git Bash, MSYS2, Cygwin): bash.exe launched by cmd.exe may not have
# /usr/bin (stat, date, cksum) on PATH.
case "${OSTYPE:-}" in msys*|cygwin*) PATH="/usr/bin:$PATH" ;; esac

NODE="$(command -v node)"
[ -z "$NODE" ] && exit 0

export DEVICE_HOME="$HOME" DEVICE_CACHE="${TMPDIR:-/tmp}"
case "${OSTYPE:-}" in msys*|cygwin*) DEVICE_HOME="${USERPROFILE:-$HOME}" ;; esac
export DEVICE_USER="${USER:-${USERNAME:-user}}"

"$NODE" -e '
const fs = require("fs"), os = require("os"), path = require("path"), cp = require("child_process");
const env = process.env;

function run(cmd, args) {
  try { return cp.execFileSync(cmd, args, { encoding: "utf8", timeout: 1500, stdio: ["ignore", "pipe", "ignore"] }); }
  catch (e) { return ""; }
}

// --- CPU: all cores, between this render and the previous one -------------
function cpuTimes() {
  let idle = 0, total = 0;
  for (const c of os.cpus()) {
    const t = c.times;
    idle += t.idle;
    total += t.user + t.nice + t.sys + t.idle + t.irq;
  }
  return { idle, total };
}
const cachePath = path.join(env.DEVICE_CACHE || os.tmpdir(), "ccstatusline-device-" + (env.DEVICE_USER || "user").replace(/[^\w.-]/g, "_") + ".json");

function cpuPercent(done) {
  const now = Date.now(), cur = cpuTimes();
  let prev = null;
  try { prev = JSON.parse(fs.readFileSync(cachePath, "utf8")); } catch (e) {}
  const pct = (a, b) => {
    const dt = b.total - a.total;
    return dt > 0 ? Math.min(Math.max(100 * (1 - (b.idle - a.idle) / dt), 0), 100) : null;
  };
  const save = p => {
    try {
      const tmp = cachePath + "." + process.pid;
      fs.writeFileSync(tmp, JSON.stringify({ t: now, idle: cur.idle, total: cur.total, pct: p }));
      fs.renameSync(tmp, cachePath);
    } catch (e) {}
  };
  const age = prev ? now - prev.t : Infinity;
  if (age < 1000 && prev.pct != null) return done(prev.pct);   // too soon for a fair sample: reuse
  if (age <= 120000) {
    const p = pct(prev, cur);
    if (p != null) { save(p); return done(p); }
  }
  // No recent sample (first render, or idle for a while): measure for 200 ms.
  setTimeout(() => {
    const later = cpuTimes(), p = pct(cur, later);
    cur.idle = later.idle; cur.total = later.total;
    save(p);
    done(p);
  }, 200);
}

// --- Memory -----------------------------------------------------------------
function memPercent() {
  if (process.platform === "darwin") {
    const free = parseInt(run("/usr/sbin/sysctl", ["-n", "kern.memorystatus_level"]), 10);
    if (free >= 0 && free <= 100) return 100 - free;
  }
  if (process.platform === "linux") {
    try {
      const m = fs.readFileSync("/proc/meminfo", "utf8");
      const kb = k => { const r = m.match(new RegExp("^" + k + ":\\s+(\\d+)", "m")); return r ? +r[1] : NaN; };
      const total = kb("MemTotal"), avail = kb("MemAvailable");
      if (total > 0 && avail >= 0) return 100 * (1 - avail / total);
    } catch (e) {}
  }
  const total = os.totalmem();
  return total > 0 ? 100 * (1 - os.freemem() / total) : null;
}

// --- Disk -------------------------------------------------------------------
function diskPercent() {
  const out = run("df", ["-Pk", env.DEVICE_HOME || os.homedir()]).trim().split("\n").pop() || "";
  const f = out.split(/\s+/);
  // Filesystem, 1024-blocks, Used, Available, Capacity, Mounted on (the name may hold spaces: count from the end)
  if (f.length < 6) return null;
  const n = f.length, size = +f[n - 5], used = +f[n - 4], avail = +f[n - 3];
  if (!(size > 0) || !(avail >= 0)) return null;
  if (process.platform === "darwin") return 100 * (1 - avail / size);
  return used + avail > 0 ? 100 * used / (used + avail) : null;
}

// --- Render -----------------------------------------------------------------
const GREEN = [63, 185, 80], YELLOW = [210, 153, 34], RED = [248, 81, 73];
const TRUECOLOR = /truecolor|24bit/i.test(env.COLORTERM || "");
function colour(rgb) {
  const [r, g, b] = rgb;
  if (TRUECOLOR) return "\x1b[38;2;" + r + ";" + g + ";" + b + "m";
  const q = c => c < 48 ? 0 : c < 115 ? 1 : Math.floor((c - 35) / 40);
  return "\x1b[38;5;" + (16 + 36 * q(r) + 6 * q(g) + q(b)) + "m";
}
const RESET = "\x1b[0m", DIM = "\x1b[2m";
const CELLS = 6;

function level(p, sustained) {
  if (p >= 85) return RED;
  if (p >= 60 || sustained) return YELLOW;
  return GREEN;
}
function seg(label, p, sustained) {
  if (p == null || !isFinite(p)) return "";
  p = Math.min(Math.max(p, 0), 100);
  const filled = Math.round(p / 100 * CELLS);
  return label + " " + colour(level(p, sustained)) + "█".repeat(filled) + DIM + "░".repeat(CELLS - filled) + RESET +
    colour(level(p, sustained)) + " " + Math.round(p) + "%" + RESET;
}

// Test hook: STATUSLINE_DEVICE_SAMPLE={"cpu":..,"load15":..,"cores":..,"mem":..,"disk":..} skips measuring.
let sample = null;
try { sample = env.STATUSLINE_DEVICE_SAMPLE ? JSON.parse(env.STATUSLINE_DEVICE_SAMPLE) : null; } catch (e) {}

function render(cpu) {
  const s = sample || {};
  const cores = sample ? s.cores : os.cpus().length;
  const load15 = sample ? s.load15 : os.loadavg()[2];
  const mem = sample ? s.mem : memPercent();
  const disk = sample ? s.disk : diskPercent();
  const sustained = cores > 0 && load15 > 0 && load15 / cores >= 0.30;
  const out = [seg("CPU", cpu, sustained), seg("Mem", mem, false), seg("Disk", disk, false)].filter(Boolean);
  process.stdout.write(out.join(" | "));
}

if (sample) render(sample.cpu);
else cpuPercent(render);
'
