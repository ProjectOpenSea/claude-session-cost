---
description: Report the current session's estimated spend (and budget headroom if configured)
argument-hint: "[session-id|--all] [--json]"
allowed-tools: Bash(python3:*)
---
Report the current session's estimated spend.

Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/session-cost.py "$ARGUMENTS"
```

`"$ARGUMENTS"` is double-quoted on purpose: the whole tail reaches the script
as a single shell argument (no metacharacter expansion at the shell boundary).
The script `shlex.split`s it internally — same tokenization as bash
word-splitting, with no command execution.

- With no arguments it reports the **newest** cost file, which is the invoking
  session (this plugin's PostToolUse hook updates it on every tool call). If
  other sessions are running concurrently, the newest file can briefly belong
  to a sibling — the report includes the file mtime so this is visible.
- Pass a session ID to report a specific session.
- Pass `--all` for a table of every tracked session, newest first.

Present the script's markdown output to the user verbatim (it already includes
spend, tool calls, and budget soft/hard limit usage from `~/.claude/budgets.json`
when that file exists). Do not re-derive or re-estimate costs yourself.

If the script reports no cost files, explain that the plugin's tracking hook
has not recorded anything yet — costs accrue from the first tool call after
the plugin is installed, and the numbers are estimates from per-tool token
averages, not actual API usage.
