# Changelog

## 0.1.0 — 2026-06-10

Initial release.

- `PostToolUse` hook accumulating estimated per-tool-call cost into
  session-scoped cost files (atomic writes, `0600`, fail-open).
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
