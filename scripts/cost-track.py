#!/usr/bin/env python3
"""PostToolUse hook — accumulates an estimated cost per tool call.

Writes /tmp/claude-cost-{session_id}.json (dir overridable via COST_DIR /
CLAUDE_TMP_DIR) with running totals the /session-cost:report command reads.

Fail-open by design: cost tracking is advisory, so every failure path exits 0
and never blocks the tool pipeline. Writes are atomic (mkstemp + os.replace)
with 0600 permissions.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_tracker import atomic_write_json, estimate_tool_cost, resolve_cost_dir  # noqa: E402

# session_id is spliced into a filename — restrict to the id alphabet.
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")


def track(data):
    session_id = data.get("session_id", "")
    if not session_id or not _SESSION_ID_RE.fullmatch(session_id):
        return
    tool_name = data.get("tool_name", "")
    model = data.get("model", "")  # absent on PostToolUse today; future-proof

    cost_dir = resolve_cost_dir()
    cost_file = os.path.join(cost_dir, f"claude-cost-{session_id}.json")

    totals = {}
    try:
        with open(cost_file) as f:
            totals = json.load(f)
        if not isinstance(totals, dict):
            totals = {}
    except (OSError, json.JSONDecodeError, ValueError):
        totals = {}

    totals["total_estimated_usd"] = (
        totals.get("total_estimated_usd", 0) + estimate_tool_cost(tool_name, model)
    )
    totals["tool_calls"] = totals.get("tool_calls", 0) + 1
    totals["last_updated"] = datetime.now(timezone.utc).isoformat()
    totals["model"] = model or totals.get("model", "unknown")
    team_name = os.environ.get("CLAUDE_TEAM_NAME")
    if team_name:
        totals["team_name"] = team_name

    try:
        atomic_write_json(cost_file, totals)
    except OSError:
        pass


def main():
    try:
        track(json.load(sys.stdin))
    except Exception:
        pass  # advisory — never block the pipeline
    sys.exit(0)


if __name__ == "__main__":
    main()
