# Work and personal profiles, and account rotation

This guide covers two separate things:

1. **Separate profiles.** Keep a work Claude Code login and a personal one side by side. Plain `claude` is work and `claude-personal` is personal, each with its own login, history, settings and plugins. This works anywhere Claude Code runs.
2. **Rotation.** If you have more than one personal subscription, `rclaude` starts Claude Code on the first one. When that account hits its session or weekly limit, it carries on with the same conversation on the next account. When a higher account resets, it moves back up. Rotation runs on macOS and Linux (including WSL).

You can use part 1 on its own. Part 2 builds on it.

---

## Part 1: separate work and personal profiles

Claude Code keeps everything for one account in a **config dir**. By default that's `~/.claude`, plus `~/.claude.json` in your home folder. Set `CLAUDE_CONFIG_DIR` to another folder and you get a second, completely separate profile.

| Kept separate per profile | Shared between profiles |
|---|---|
| Login | Project `.claude/` folders and `CLAUDE.md` files |
| `.claude.json` (inside the config dir when `CLAUDE_CONFIG_DIR` is set) | MCP servers in a project's `.mcp.json` |
| Conversation history, settings, plugins, memory | git, gh and cloud CLI logins |
| | Anything else outside the config dir |

Where each profile's login is stored:

- **macOS:** in the Keychain. The default profile uses the item `Claude Code-credentials`. Any other config dir gets `Claude Code-credentials-<8 hex characters>`, where the suffix is the start of the SHA-256 of the dir's path.
- **Linux:** in `<config dir>/.credentials.json`, readable only by you.

### Set it up

```bash
mkdir -p ~/.claude-personal
CLAUDE_CONFIG_DIR=~/.claude-personal claude      # then /login with your personal account, then /exit
```

Then add an alias to your shell profile (`~/.zshrc` or `~/.bashrc`):

```bash
alias claude-personal='CLAUDE_CONFIG_DIR=~/.claude-personal claude'
```

Plain `claude` keeps using your work login, and `claude-personal` uses the personal one. (Part 2 points this alias at `rclaude` instead.)

> **Don't set `CLAUDE_CONFIG_DIR=~/.claude` for the default profile.** On macOS that path gets a *different* Keychain item from the default, so you'd land in a separate, logged-out profile. For the default profile, leave the variable unset.

### Status line in both profiles

The kit's `install.sh` points only `~/.claude/settings.json` at the status line. For the personal profile, add this to `~/.claude-personal/settings.json`:

```json
"statusLine": { "type": "command", "command": "ccstatusline" }
```

The cost widgets track each config dir separately, and row 4 shows the account in use.

### claude-squad

claude-squad starts the program you give it with `-p`. To keep the personal profile there, pass the profile too, e.g. `claude-squad -p "env CLAUDE_CONFIG_DIR=$HOME/.claude-personal claude"`. With rotation (part 2) you pass `rclaude` instead.

---

## Part 2: rotate between personal subscriptions (`rclaude`)

> **Read this first.** Using several subscriptions to get past per-account usage limits may breach Anthropic's terms (they forbid "bypassing … protective measures"), and accounts used this way can be suspended. That's your call and your risk. **Never add an employer's account to the rotation.** Your organisation's policies apply to it, and they usually forbid exactly this.

### How it works

- `rclaude` runs `claude` with an extra settings file (`--settings`). That file adds:
  - a status-line recorder, which saves each account's 5-hour and weekly usage, then shows your normal status line with an `[account]` prefix;
  - `Stop`, `StopFailure` and `SessionStart` hooks.

  Your own settings files are never edited.
- **Moving down.** When a reply stops with "You've hit your … limit", the hook marks that account limited until its reset and stops `claude`. `rclaude` then starts it again on the next available account, with `--resume <session>` and a short "continue where you left off" prompt.
- **Moving back up.** After a higher account resets, `rclaude` switches back, but only when the session is really idle. It won't switch:
  - while a Stop hook keeps Claude working (e.g. ralph-loop);
  - while background tasks or `/loop` crons run;
  - if the conversation grew in the second before the restart;
  - until at least `min_upswitch_interval_seconds` have passed since the last switch.
- **Resumed conversations.** Resuming a conversation that ended on the *same* account's limit makes Claude Code open its limit menu before you can type. `rclaude` spots this when the session starts and switches straight away.
- **What doesn't switch:**
  - Server throttling ("Server is temporarily limiting requests") never causes a switch.
  - If every account is limited, nothing switches, and Claude Code's own wait-and-continue takes over.

### Two kinds of account

| | First account | Each extra account |
|---|---|---|
| How it logs in | The profile's normal `/login` | A `claude setup-token` token, valid for one year |
| How `rclaude` uses it | `CLAUDE_CONFIG_DIR` | The same profile, plus the token in `CLAUDE_CODE_OAUTH_TOKEN` |
| Features | Everything | No claude.ai connectors and no Remote Control (a limit of setup tokens) |

### Setup

1. **Install.** From the kit's folder:
   ```bash
   ./rotate/install.sh
   ```
   This links `~/.local/bin/rclaude` to this checkout and writes a starter `~/.config/claude-rotate/accounts.json`. It needs Python 3.9 or newer, and never touches your shell profile or tokens.

2. **Log the profile in as your first account** (part 1).

3. **Add a token for each extra account.** In a **normal terminal**, not inside Claude Code, run:
   ```bash
   claude setup-token            # sign in as the extra account in the browser
   rclaude add-token spare       # paste the token; input is hidden
   ```
   The token is saved in the macOS Keychain as `claude-rotate:spare`, or on Linux in `~/.config/claude-rotate/tokens.json` (readable only by you). Never paste a token into a Claude conversation.

4. **Edit `~/.config/claude-rotate/accounts.json`.** Accounts are tried top to bottom:
   ```json
   {
     "tiers": [
       { "name": "main",  "config_dir": "~/.claude-personal", "auth": "login", "email": "you@example.com" },
       { "name": "spare", "config_dir": "~/.claude-personal", "auth": "token", "email": "you.too@example.com" }
     ],
     "min_upswitch_interval_seconds": 120
   }
   ```

   | Field | Meaning |
   |---|---|
   | `name` | Your label for the account: letters, digits and `. _ @ -`. Token accounts are stored under it. |
   | `config_dir` | The profile to run in. Use `null` for the default profile, never `"~/.claude"`. |
   | `auth` | `"login"` (the profile's own login) or `"token"` (a token from step 3). |
   | `email` | Optional but recommended. Login accounts are checked against it at launch, so a profile logged into the wrong account is skipped. Token accounts show it in the status line. |
   | `min_upswitch_interval_seconds` | The minimum time between switches before moving back up (default 120). |

5. **Aliases.** Add to your shell profile:
   ```bash
   alias claude-personal='rclaude'      # add --dangerously-skip-permissions here if you use it
   alias csp='claude-squad -p "$HOME/.local/bin/rclaude"'
   ```

6. **Check.** `rclaude status` lists every account:
   ```
   #  ACCOUNT  STATUS     5H    WEEK  BLOCKED UNTIL         USAGE SEEN  SESSIONS  LOGIN
   1  main     limited    100%  71%   Wed 15:37 (in 1h29m)  10m ago     0         you@example.com
   2  spare    available  23%   40%   -                     1m ago      0         token
   ```
   `LOGIN` shows who each login account's profile is really logged in as, and `token` for token accounts. `5H`, `WEEK` and `USAGE SEEN` fill in once each account has been used.

### What you'll see

- Row 1 of the status line starts with `[main]`, `[spare]` and so on: the account the session runs on.
- `[main?]` means the profile is now logged into a different account than `email` says (e.g. after a `/login`). Usage isn't recorded until that's fixed.
- With this kit's status line, row 4 shows the email of the account in use, token accounts included.
- If the profile has no status line, `rclaude` shows a one-line fallback, e.g. `[main] 5h 23% · wk 40%`.

### Commands

| Command | What it does |
|---|---|
| `rclaude [claude options]` | An interactive session with rotation. Takes the same options as `claude`. |
| `rclaude status` | Usage, limits, reset times, open sessions and logins for every account. |
| `rclaude add-token <name>` | Store or replace a token (hidden input). |
| `rclaude mcp …`, `rclaude plugin …`, `rclaude auth …` | Run once on the first account's profile, with no rotation. |
| `rclaude -p …` | Print mode, run once on the best available account, with no rotation. |

---

## Day to day

- **Never `/login` inside an `rclaude` session.** It changes the login for every open session on that profile, and makes it unclear which account hit a limit. To change a profile's login:
  1. Close your `rclaude` sessions.
  2. Run `CLAUDE_CONFIG_DIR=~/.claude-personal claude`, then `/login`, then `/exit`.
  3. Open a new session.
- **Renew a token** (they last a year): `claude setup-token`, then `rclaude add-token <name>` again.
- **Add an account:** add a tier to `accounts.json` with `"auth": "token"`, then run `rclaude add-token <name>`.
- **Files:**
  - log: `~/.local/state/claude-rotate/rclaude.log`
  - state: `~/.local/state/claude-rotate/state.json`
  - config: `~/.config/claude-rotate/accounts.json`

### Troubleshooting

| You see | Do this |
|---|---|
| `rclaude: skipping spare (no token stored). Fix: rclaude add-token spare` | Run that command. |
| `rclaude: skipping main (main is logged in as …, expected …. Fix: …)` | Fix the login as above, outside running sessions. |
| No switch when every account is limited | Expected: there's nowhere to switch to. `rclaude status` shows when each account resets. |
| A switch takes a few seconds | Expected: `claude` restarts. Anything half-typed when a move-up happens at idle is lost. |

---

## Limits and risks

- **Terms of service.** Using several subscriptions to get past per-account limits may breach Anthropic's terms, and accounts can be suspended. Keep employer accounts out.
- **Token exposure.** On token accounts, the token is in the environment of everything `claude` starts: Bash commands, MCP servers and hooks. If something prints the environment, the token ends up in that conversation's transcript.
- **Options that don't carry over.** `--worktree` and `--teleport` aren't carried over on a switch. A `--settings` of your own overrides rclaude's, which turns rotation off for that session.
- **Accounts on another profile.** An account on a different profile (e.g. `"config_dir": null`) can't see the other profile's history. It gets a prompt pointing at the previous transcript instead of `--resume`.
- **Claude Code versions.** Checked against Claude Code 2.1.299. Options added later may not carry over when `rclaude` restarts `claude`.
- **Windows** isn't supported. Use WSL.

## Uninstall

```bash
./rotate/uninstall.sh
```

This removes the `rclaude` link and prints the commands to delete your accounts, state and stored tokens if you want them gone. Remove the aliases from your shell profile yourself.

## Prefer to let Claude do it?

Open Claude Code in this repo and paste:

> Read docs/accounts-and-rotation.md and the scripts in rotate/ so you know what they do. Then help me set up a separate personal profile and rclaude rotation: run ./rotate/install.sh, help me edit ~/.config/claude-rotate/accounts.json for my accounts, and tell me which commands I must run myself in a normal terminal (claude setup-token, rclaude add-token). Never ask me to paste a token into this chat.
