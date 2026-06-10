# claude-session-cost

A Claude Code plugin that tracks **estimated per-session spend** and reports it
on demand — with optional soft/hard budget headroom.

```
## Session Spend

- **Session**: `d82e570a-...`
- **Estimated spend**: $2.95
- **Tool calls tracked**: 63

### Budget
- Soft limit: $25.00 (12% used)
- Hard limit: $150.00 (2% used)
```

## Why

Claude Code's built-in `/usage` shows token usage for your account/plan, but
there's no per-session running spend estimate you can query mid-session, and
nothing that compares it to a budget you set. This plugin fills that gap with
two small stdlib-only Python pieces:

1. **A `PostToolUse` hook** (`scripts/cost-track.py`) that accumulates an
   estimated cost per tool call into `/tmp/claude-cost-{session_id}.json`
   (atomic writes, `0600` permissions, fail-open — it can never block your
   session).
2. **A `/session-cost:report` command** that reads those files and renders the
   summary above.

## Install

```
/plugin marketplace add ProjectOpenSea/claude-session-cost
/plugin install session-cost@claude-session-cost
```

Requires `python3` on your PATH (any Python ≥ 3.9, stdlib only).

## Usage

| Command | What it does |
|---|---|
| `/session-cost:report` | Current session's estimated spend |
| `/session-cost:report <session-id>` | A specific session |
| `/session-cost:report --all` | Every tracked session, newest first |
| `/session-cost:report --json` | Machine-readable output |

## Budgets (optional)

Create `~/.claude/budgets.json` to get headroom reporting:

```json
{
  "default": {
    "session_soft_limit_usd": 5.0,
    "session_hard_limit_usd": 25.0
  },
  "projects": {
    "my-project": { "session_soft_limit_usd": 20.0 }
  }
}
```

Project overrides match the directory name under `~/code/<project>` or
`~/Code/<project>`. A limit of `0` is valid and renders as a hard stop. This
plugin **reports** against budgets; it does not block tool calls when you
exceed them (enforcement is a deliberate non-goal — see Caveats).

## How estimates work — read this

- Costs are **estimates from per-tool token averages** (e.g. an `Edit` ≈ 2000
  input + 500 output tokens), not actual API usage. `PostToolUse` hooks don't
  receive token counts or the model name, so the value is in trend and order
  of magnitude, not cents-accuracy.
- Without a model name, pricing defaults to **Opus-tier** ($5/$25 per MTok).
  Sonnet/Haiku sessions will be overestimated.
- "Current session" is the **newest cost file**. If multiple sessions run
  concurrently, the newest file can briefly belong to a sibling session — the
  report prints the file's mtime so you can tell.

## Where state lives

`{COST_DIR | CLAUDE_TMP_DIR | /tmp}/claude-cost-{session_id}.json` — one small
JSON file per session, owner-readable only (`0600`). On a multi-user host,
note that filenames (session IDs) are visible to other users in `/tmp`; file
*contents* are not. Files are not auto-pruned; they're tiny, but you can
delete `claude-cost-*.json` freely.

If `CLAUDE_TEAM_NAME` is set in the environment, it's recorded in each cost
file and the report shows a team-wide total across sessions sharing it.

## Caveats / non-goals

- **No enforcement.** This plugin never blocks a tool call. If you want a
  PreToolUse budget gate (warn at soft limit, deny at hard limit), that's a
  natural extension — PRs welcome.
- **Not a billing source of truth.** Use the Anthropic Console for real spend.

## Development

```sh
python -m pytest tests/ -v
claude plugin validate .
```

Everything is stdlib-only Python; no dependencies to install.

## License

MIT
