#!/usr/bin/env python3
"""PreToolUse hook — enforces session/team budget limits.

Reads the running spend written by cost-track.py and compares it against
limits in ~/.claude/budgets.json:

- under soft limit:    allow (prints {})
- at/above soft limit: warn via systemMessage, deduplicated per $1 increment
- at/above hard limit: BLOCK the tool call (exit 2, reason on stderr)
- any error / no budgets.json / no cost file: allow (fail-open)

A configured limit of 0 is valid and means "block everything" (hard) or
"warn immediately" (soft). Without ~/.claude/budgets.json this hook is a
no-op, so installing the plugin never blocks anyone by surprise.
"""
import glob
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_tracker import atomic_write_json, resolve_cost_dir  # noqa: E402

DEFAULT_BUDGETS_PATH = os.path.join(Path.home(), ".claude", "budgets.json")
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _resolve_limits(config, project=None):
    limits = dict(config.get("default", {}))
    if project and project in config.get("projects", {}):
        limits.update(config["projects"][project])
    return limits


def _get_team_total(cost_dir, team_name):
    total = 0.0
    for path in glob.glob(os.path.join(cost_dir, "claude-cost-*.json")):
        data = _load_json(path)
        if data and data.get("team_name") == team_name:
            total += data.get("total_estimated_usd", 0)
    return total


def _detect_project(cwd):
    parts = (cwd or "").split("/")
    for i, part in enumerate(parts):
        if part.lower() == "code" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def check_budget(session_id, cost_dir, budgets_path, project=None, team_name=None):
    """Return {"action": "allow"|"warn"|"block", ...}. Gates on `is not None`
    so a zero-dollar limit is honored as a hard stop."""
    config = _load_json(budgets_path)
    if not config:
        return {"action": "allow"}
    limits = _resolve_limits(config, project)

    cost_file = os.path.join(cost_dir, f"claude-cost-{session_id}.json")
    cost_data = _load_json(cost_file)
    if not cost_data:
        return {"action": "allow"}
    current = cost_data.get("total_estimated_usd", 0)

    team_name = team_name or cost_data.get("team_name")
    if team_name:
        team_total = _get_team_total(cost_dir, team_name)
        team_hard = limits.get("team_hard_limit_usd")
        if team_hard is not None and team_total >= team_hard:
            return {
                "action": "block",
                "message": (
                    f"TEAM BUDGET EXCEEDED: team '{team_name}' total ~${team_total:.2f} "
                    f">= hard limit ${team_hard:.2f}. Raise the limit in "
                    f"~/.claude/budgets.json to continue."
                ),
            }
        team_soft = limits.get("team_soft_limit_usd")
        if team_soft is not None and team_total >= team_soft:
            return {
                "action": "warn",
                "systemMessage": (
                    f"Team budget advisory: team '{team_name}' spend ~${team_total:.2f} "
                    f"(soft limit ${team_soft:.2f}). Consider wrapping up."
                ),
            }

    hard = limits.get("session_hard_limit_usd")
    if hard is not None and current >= hard:
        return {
            "action": "block",
            "message": (
                f"BUDGET EXCEEDED: session spend ~${current:.2f} >= hard limit "
                f"${hard:.2f}. Raise the limit in ~/.claude/budgets.json or start "
                f"a new session."
            ),
        }

    soft = limits.get("session_soft_limit_usd")
    if soft is not None and current >= soft:
        # None = never warned (first warn fires even at $0.00 spend);
        # afterwards re-warn only per $1 increment.
        last_warn = cost_data.get("last_budget_warn_usd")
        if last_warn is None or current - last_warn >= 1.0:
            cost_data["last_budget_warn_usd"] = current
            if not os.path.islink(cost_file):
                try:
                    atomic_write_json(cost_file, cost_data)
                except OSError:
                    pass
            return {
                "action": "warn",
                "systemMessage": (
                    f"Budget advisory: session spend ~${current:.2f} "
                    f"(soft limit ${soft:.2f}"
                    + (f", hard limit ${hard:.2f}" if hard is not None else "")
                    + "). Consider wrapping up."
                ),
            }

    return {"action": "allow"}


def main():
    try:
        data = json.load(sys.stdin)
        session_id = data.get("session_id", "")
        if not session_id or not _SESSION_ID_RE.fullmatch(session_id):
            print("{}")
            return
        result = check_budget(
            session_id=session_id,
            cost_dir=resolve_cost_dir(),
            budgets_path=os.environ.get("BUDGET_CONFIG_PATH", DEFAULT_BUDGETS_PATH),
            project=_detect_project(data.get("cwd") or os.environ.get("PWD", "")),
            team_name=os.environ.get("CLAUDE_TEAM_NAME"),
        )
    except Exception:
        print("{}")  # fail-open: never block the pipeline on our own errors
        return

    if result["action"] == "block":
        sys.stderr.write(result.get("message", "Budget exceeded") + "\n")
        sys.exit(2)
    elif result["action"] == "warn":
        print(json.dumps({"systemMessage": result["systemMessage"]}))
    else:
        print("{}")


if __name__ == "__main__":
    main()
