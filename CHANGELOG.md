# Changelog

## 0.1.0 — 2026-06-10

Initial release.

- `PostToolUse` hook pricing **actual API usage from the session transcript**
  (input/output/cache tokens at per-model rates, requestId-deduplicated,
  incremental byte-offset reads), falling back to per-tool token estimates
  when no transcript is readable. Session-scoped cost files with atomic
  writes, `0600`, fail-open.
- `PreToolUse` budget gate: soft-limit warnings (deduplicated per $1) and
  hard-limit tool-call blocking from `~/.claude/budgets.json`; zero-dollar
  limits honored as kill switches; no-op without a budgets file; fail-open.
- `/session-cost:report` command: current session, explicit session id,
  `--all`, `--json`.
- Optional budget headroom from `~/.claude/budgets.json` with per-project
  overrides; zero-dollar limits supported.
- Team totals via `CLAUDE_TEAM_NAME`.
- Pricing table: Fable 5, Opus 4.5–4.8, Sonnet 4.5/4.6, Haiku 4.5
  (Anthropic published rates, 2026).

## 0.1.1 — 2026-06-10

- Fix: remove duplicate `hooks` declaration from plugin.json — `hooks/hooks.json`
  is auto-loaded by convention and redeclaring it fails plugin load with
  "Duplicate hooks file detected". Caught by live install verification.

## 0.1.2 — 2026-06-10

- Fix: price 1-hour-TTL cache writes at 2x base input (5-minute at 1.25x)
  using the per-TTL `cache_creation` breakdown in transcript usage blocks.
  Claude Code uses 1h cache exclusively, so flat 1.25x underpriced cache
  writes by 1.6x. Verified against live pricing docs and a real transcript.
