# Claude Code Statusline Kit

A drop-in, multi-row status line for [Claude Code](https://claude.com/claude-code) that shows your **spend, context usage, git state, and which account you're logged in as** — at a glance, on every render.

It's a preset for [`ccstatusline`](https://github.com/sirmalloc/ccstatusline) plus six small widget scripts. Costs come from [`ccusage`](https://github.com/ryoppippi/ccusage).

```
Cost: $6.50 | Day: $18.40 | Wk: $92.10 | Mo: $412.75
Model: Opus 4.8 (1M context) | Ctx: 24.3% (243k) | Session: 1h 12m
claude-statusline-kit | ⎇ main | (+14,-3)
👤 you@example.com
```

| Row | Shows |
|-----|-------|
| **1 — Money** | Session cost · today · week-to-date · month-to-date |
| **2 — Context** | Model · context window used (correct for 1M-context models) with live token count · session duration |
| **3 — Git** | Repo name · branch · uncommitted changes. Hidden automatically when you're not in a repo. |
| **4 — Account** | The Claude account currently logged in (updates when you `/login`). Optional Work/Personal tag. |

## Why

- **Accurate context %** — Claude Code's default indicators (and `ccstatusline`'s built-in widget) scale the context bar to 200k. On 1M-context models (Opus 4.6+, Sonnet 4.6+, the Claude 5 family) that makes the number sail past 100%. This kit knows each model's real window.
- **Real spend, always priced** — the cost widgets fetch live pricing, so brand-new models are priced correctly instead of showing `$0.00`.
- **Account awareness** — if you switch between a work and a personal Claude login, row 4 tells you which one is active right now.

## Requirements

You provide:

- **[Claude Code](https://claude.com/claude-code)** — this is a status line for it.
- **Node.js 18+** and **npm** — `ccstatusline`, `ccusage`, and the widget scripts all run on Node.
  Install from [nodejs.org](https://nodejs.org), or `brew install node` (macOS) / your distro's package manager (Linux).
- **macOS or Linux** with `bash`. On Windows, use WSL.
- **`curl`** (optional) — used to self-update context-window sizes. Present by default on macOS and most Linux; if absent, the kit falls back to a built-in model list.

The installer handles the rest:

- Installs **`ccstatusline`** (renders the status line) and **`ccusage`** (provides the cost data) via `npm install -g` if they're missing.
- No API keys, accounts, or data files to set up. Cost and context-window data come from live sources at runtime (see [How it works](#how-it-works-and-privacy)).

## Install

```bash
git clone https://github.com/Obednal97/claude-statusline-kit.git
cd claude-statusline-kit
./install.sh
```

Then open a new Claude Code session (or wait for the next status render). The installer backs up anything it overwrites (`*.bak-<timestamp>`).

### Prefer to let Claude do it?

Point Claude Code at this repo and paste:

> Read the scripts in this repo so you know what they do, then run `./install.sh` to set up my Claude Code status line. Afterwards, confirm it renders and tell me if `ccstatusline` or `ccusage` still need installing or aren't on my PATH.

## Configure

- **Tag work vs personal accounts** — edit `~/.config/ccstatusline/account.sh` and set `WORK_DOMAIN` to your work email domain (e.g. `WORK_DOMAIN="acme.com"`). Then row 4 shows `👤 Work · you@acme.com` or `👤 Personal · you@personal.com`. Leave it empty for just the email.
- **Change widgets, colours, layout, powerline** — run `ccstatusline` for its interactive editor. Your config lives at `~/.config/ccstatusline/settings.json`.
- **Turn off the context-window auto-update** — set `USE_LIVE_WINDOWS=0` near the top of `~/.config/ccstatusline/context-percentage.sh` to skip the network fetch and use only the built-in model list.

## Keeping it up to date

Two kinds of "up to date", handled separately:

- **Pricing and context-window sizes update themselves** — no action needed. Pricing comes from `ccusage`, and context windows are fetched from the same [community model database](https://github.com/BerriAI/litellm) (cached ~daily, refreshed in the background). When a new Claude model ships, it gets priced and scaled correctly automatically, without a reinstall. Offline, the kit falls back to a built-in model list.
- **The kit's code** (new features, fixes) updates via git:

  ```bash
  ./update.sh          # git pull + re-run install.sh
  ```

  Or manually: `git pull && ./install.sh`. The installer is idempotent and backs up anything it changes.

## How it works (and privacy)

- Everything runs **locally**. The only network calls are the ones that fetch **public** pricing/model data (via `ccusage` and the LiteLLM model database) — no personal data ever leaves your machine.
- The account widget reads only your **email/identity** from `~/.claude.json` (`oauthAccount`). It never reads or displays credentials or tokens.
- The cost widgets shell out to `ccusage`, which reads your local Claude usage logs and fetches public pricing. `ccusage` takes ~10s, so it always runs in a detached background refresh and the widgets only read a `/tmp` cache (refreshed every 15 minutes). The first render of a new day/week/month shows `…` for a few seconds until the first refresh lands.
- The context widget reads the current session transcript that Claude Code passes it on stdin, and divides tokens-used by the model's real context window (looked up from the cached model database, with a built-in fallback).

## Uninstall

```bash
./uninstall.sh
```

Removes the kit's scripts + config and unsets the status line. `ccstatusline`/`ccusage` are left installed; remove them with `npm uninstall -g ccstatusline ccusage` if you want.

## Acknowledgements

This kit is a thin preset — the real work is done by these projects, with thanks to their authors and contributors:

- **[ccstatusline](https://github.com/sirmalloc/ccstatusline)** by [@sirmalloc](https://github.com/sirmalloc) — MIT. The Claude Code status-line renderer this kit configures and extends with custom widgets.
- **[ccusage](https://github.com/ryoppippi/ccusage)** by [@ryoppippi](https://github.com/ryoppippi) — MIT. Reads your local Claude usage and prices it; powers the cost widgets (session / day / week / month).
- **[LiteLLM](https://github.com/BerriAI/litellm)** by [BerriAI](https://github.com/BerriAI) — MIT. Its public [`model_prices_and_context_window.json`](https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json) dataset is the source of live pricing (via ccusage) and the model context-window sizes used to scale the context %.
- **[Claude Code](https://claude.com/claude-code)** by Anthropic — the tool this status line runs in.

This kit is an independent preset and is not affiliated with or endorsed by any of the above projects.

## License

MIT — see [LICENSE](LICENSE).
