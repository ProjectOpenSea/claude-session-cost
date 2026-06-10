#!/usr/bin/env python3
"""Session cost reporter — backs the /session-cost:report command.

Reads the cost files written by cost-track.py (the plugin's PostToolUse hook)
and optional budget limits from ~/.claude/budgets.json, and prints the
current session's estimated spend.

"Current session" defaults to the most-recently-modified cost file: the hook
updates the invoking session's file on every tool call, so at invocation time
it is the newest. Concurrent sessions can race within seconds — the report
renders the file mtime to make that visible, and an explicit session ID
overrides the heuristic.

Usage:
    session-cost.py                  # newest cost file (assumed current session)
    session-cost.py <session-id>     # explicit session
    session-cost.py --all            # every tracked session, newest first
    session-cost.py [...] --json     # machine-readable output

Env overrides: COST_DIR / CLAUDE_TMP_DIR (default /tmp), BUDGET_CONFIG_PATH
(default ~/.claude/budgets.json).
"""
import glob
import json
import os
import re
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_tracker import resolve_cost_dir  # noqa: E402

DEFAULT_BUDGETS_PATH = os.path.join(Path.home(), ".claude", "budgets.json")
_COST_PREFIX = "claude-cost-"
# Operator-supplied session ids are spliced into a filesystem path — restrict
# to the id alphabet so traversal/metacharacters can't reach the path join.
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _resolve_limits(budgets_path, project=None):
    """Budget limits from budgets.json, project overrides merged on defaults."""
    config = _load_json(budgets_path)
    if not config:
        return {}
    limits = dict(config.get("default", {}))
    if project and project in config.get("projects", {}):
        limits.update(config["projects"][project])
    return limits


def _get_team_total(cost_dir, team_name):
    """Sum total_estimated_usd across all cost files matching team_name."""
    total = 0.0
    for path in glob.glob(os.path.join(cost_dir, _COST_PREFIX + "*.json")):
        data = _load_json(path)
        if data and data.get("team_name") == team_name:
            total += data.get("total_estimated_usd", 0)
    return total


def _detect_project(cwd=None):
    """Project name from a ~/code/<project> or ~/Code/<project> style cwd."""
    cwd = cwd or os.environ.get("PWD", os.getcwd())
    parts = cwd.split("/")
    for i, part in enumerate(parts):
        if part.lower() == "code" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def _session_id_from_path(path):
    name = os.path.basename(path)
    return name[len(_COST_PREFIX):-len(".json")]


def _cost_files(cost_dir):
    """All cost files in cost_dir as (mtime, path), newest first."""
    entries = []
    for path in glob.glob(os.path.join(cost_dir, _COST_PREFIX + "*.json")):
        try:
            entries.append((os.path.getmtime(path), path))
        except OSError:
            continue
    return sorted(entries, reverse=True)


def find_current_session(cost_dir):
    """Session ID of the most-recently-updated cost file, or None."""
    files = _cost_files(cost_dir)
    return _session_id_from_path(files[0][1]) if files else None


def session_report(session_id, cost_dir, budgets_path=DEFAULT_BUDGETS_PATH, project=None):
    """Build a spend report dict for one session, or None if unreadable."""
    cost_file = os.path.join(cost_dir, f"{_COST_PREFIX}{session_id}.json")
    data = _load_json(cost_file)
    if not data:
        return None

    total = data.get("total_estimated_usd", 0)
    limits = _resolve_limits(budgets_path, project)

    report = {
        "session_id": session_id,
        "total_usd": total,
        "tool_calls": data.get("tool_calls"),
        "model": data.get("model"),
        "last_updated": data.get("last_updated"),
        "limits": limits,
    }

    try:
        mtime = os.path.getmtime(cost_file)
        report["file_mtime"] = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
    except OSError:
        pass

    # A limit of 0 is valid (hard stop) — gate on None, not truthiness.
    # Percentages are only meaningful for positive limits.
    soft = limits.get("session_soft_limit_usd")
    hard = limits.get("session_hard_limit_usd")
    if soft is not None and soft > 0:
        report["pct_of_soft"] = round(total / soft * 100, 1)
    if hard is not None and hard > 0:
        report["pct_of_hard"] = round(total / hard * 100, 1)

    team_name = data.get("team_name")
    if team_name:
        report["team"] = {
            "name": team_name,
            "total_usd": _get_team_total(cost_dir, team_name),
        }

    return report


def render_markdown(report):
    """Render a session report as a short markdown summary."""
    lines = [
        "## Session Spend",
        "",
        f"- **Session**: `{report['session_id']}`",
        f"- **Estimated spend**: ${report['total_usd']:.2f}",
    ]
    if report.get("tool_calls") is not None:
        lines.append(f"- **Tool calls tracked**: {report['tool_calls']}")
    if report.get("model"):
        lines.append(f"- **Model**: {report['model']}")
    if report.get("last_updated"):
        lines.append(f"- **Last updated**: {report['last_updated']}")
    if report.get("file_mtime"):
        lines.append(
            f"- **Cost file mtime**: {report['file_mtime']} "
            f"(newest-file heuristic — if this isn't within seconds of now, "
            f"a concurrent session may own this file)"
        )

    limits = report.get("limits") or {}
    soft = limits.get("session_soft_limit_usd")
    hard = limits.get("session_hard_limit_usd")
    if soft is not None or hard is not None:
        lines.append("")
        lines.append("### Budget")
        if soft is not None:
            pct = report.get("pct_of_soft")
            suffix = f" ({pct:.0f}% used)" if pct is not None else ""
            lines.append(f"- Soft limit: ${soft:.2f}{suffix}")
        if hard is not None:
            pct = report.get("pct_of_hard")
            suffix = f" ({pct:.0f}% used)" if pct is not None else ""
            lines.append(f"- Hard limit: ${hard:.2f}{suffix}")

    team = report.get("team")
    if team:
        lines.append("")
        lines.append(f"**Team `{team['name']}` total**: ${team['total_usd']:.2f}")

    lines.append("")
    lines.append("_Estimates from per-tool token averages, not actual API usage._")
    return "\n".join(lines)


def render_all(cost_dir):
    """Render every tracked session as a markdown table, newest first."""
    rows = []
    total = 0.0
    skipped = 0
    for _, path in _cost_files(cost_dir):
        data = _load_json(path)
        if not data:
            skipped += 1
            continue
        usd = data.get("total_estimated_usd", 0)
        total += usd
        rows.append(
            f"| {_session_id_from_path(path)} | {data.get('model', '?')} | "
            f"{data.get('tool_calls', '?')} | ${usd:.4f} |"
        )
    lines = [
        "## Tracked Sessions (newest first)",
        "",
        "| Session | Model | Tool Calls | Est. Cost |",
        "|---|---|---|---|",
        *rows,
        "",
        f"**Total tracked spend: ${total:.2f}**",
    ]
    if skipped:
        lines.append(f"\n_({skipped} unreadable cost file(s) skipped — total may underreport.)_")
    return "\n".join(lines)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # The /session-cost:report command hands the whole operator tail as ONE
    # quoted shell argument ("$ARGUMENTS"). shlex.split it here — same
    # tokenization as bash word-splitting, but with no command execution.
    if len(argv) == 1:
        argv = shlex.split(argv[0])
    as_json = "--json" in argv
    show_all = "--all" in argv
    args = [a for a in argv if not a.startswith("--")]

    if args and not _SESSION_ID_RE.fullmatch(args[0]):
        sys.stderr.write(f"Invalid session id: {args[0]!r}\n")
        return 1

    cost_dir = resolve_cost_dir()
    budgets_path = os.environ.get("BUDGET_CONFIG_PATH", DEFAULT_BUDGETS_PATH)
    project = _detect_project()

    if show_all:
        files = _cost_files(cost_dir)
        if not files:
            sys.stderr.write(f"No cost tracking files found in {cost_dir}\n")
            return 1
        if as_json:
            reports = [
                session_report(_session_id_from_path(p), cost_dir, budgets_path, project)
                for _, p in files
            ]
            print(json.dumps([r for r in reports if r], indent=2))
        else:
            print(render_all(cost_dir))
        return 0

    session_id = args[0] if args else find_current_session(cost_dir)
    if not session_id:
        sys.stderr.write(
            f"No cost tracking files found in {cost_dir}. The session-cost "
            f"plugin's PostToolUse hook writes them — has it run in a session yet?\n"
        )
        return 1

    report = session_report(session_id, cost_dir, budgets_path, project)
    if report is None:
        sys.stderr.write(f"No readable cost file for session '{session_id}' in {cost_dir}\n")
        return 1

    print(json.dumps(report, indent=2) if as_json else render_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
