# claude-session-cost

A Claude Code plugin that tracks **estimated per-session spend**, reports it
on demand, and (optionally) **enforces budgets** — warning at a soft limit and
blocking tool calls at a hard limit.

```
## Session Spend

- **Session**: `d82e570a-...`
- **Estimated spend**: $2.95
- **Tool calls tracked**: 63

### Budget
- Soft limit: $5.00 (59% used)
- Hard limit: $25.00 (12% used)
```

## Why

Claude Code's built-in `/usage` shows token usage for your account/plan, but
there's no per-session running spend estimate you can query mid-session, and
nothing that compares it to a budget you set. This plugin fills that gap with
three small stdlib-only Python pieces:

1. **A `PostToolUse` hook** (`scripts/cost-track.py`) that accumulates an
   estimated cost per tool call into `/tmp/claude-cost-{session_id}.json`
   (atomic writes, `0600` permissions, fail-open — its own errors can never
   block your session).
2. **A `PreToolUse` budget gate** (`scripts/budget-guard.py`) that compares
   the running total against `~/.claude/budgets.json`: warns Claude via a
   system message at the soft limit (deduplicated per $1), and **blocks tool
   calls** at the hard limit. Without a `budgets.json` it is a no-op, so
   installing the plugin never blocks anyone by surprise.
3. **A `/session-cost:report` command** that reads the cost files and renders
   the summary above.

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
`~/Code/<project>`. A limit of `0` is valid — a zero hard limit blocks every
tool call (useful as a kill switch). `team_soft_limit_usd` /
`team_hard_limit_usd` aggregate across sessions sharing a `CLAUDE_TEAM_NAME`.

When you hit a hard limit, the gate denies tool calls with the reason on
stderr; raise the limit in `budgets.json` or start a new session. The gate is
fail-open: any error in the gate itself (corrupt config, unreadable cost
file) allows the tool call rather than wedging your session.

## How costs are computed — read this

- **Primary path: transcript actuals.** On every tool call the hook reads the
  *new tail* of the session transcript (`transcript_path` from hook stdin) and
  prices the ground-truth `usage` blocks from assistant entries — real
  input/output tokens, **cache reads at ~0.1× and cache writes at ~1.25×**
  (cache reads dominate input tokens in agentic sessions, so ignoring them
  makes estimates wildly wrong), at the **actual model's** published rates.
  Entries are deduplicated by `requestId` and the file is read incrementally
  via a stored byte offset, so the per-call overhead stays tiny.
- **Fallback: per-tool averages.** If no transcript is readable, the hook
  falls back to static per-tool token estimates priced at Opus-tier rates.
  The report labels which basis produced the number.
- **Known gap:** the transcript records usage as Claude Code writes it; the
  final API call of a turn may not be priced until the next tool call lands.
  Numbers are near-real-time, not to-the-cent live.
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

- **One writer per cost file.** If another tool on your machine also writes
  `claude-cost-{session_id}.json` (e.g. a custom harness with its own
  PostToolUse cost hook), totals will mix and inflate. Disable the other
  writer or point this plugin elsewhere via `COST_DIR`.

- **Enforcement is only as good as the estimates.** The hard-limit gate
  blocks on *estimated* spend (see above) — treat limits as guardrails with
  margin, not precise meters.
- **Not a billing source of truth.** Use the Anthropic Console for real spend.

## Development

```sh
python -m pytest tests/ -v
claude plugin validate .
```

Everything is stdlib-only Python; no dependencies to install.

## License

MIT
