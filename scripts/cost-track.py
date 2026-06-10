#!/usr/bin/env python3
"""PostToolUse hook — accumulates session cost from transcript actuals.

On every tool call this reads the NEW tail of the session transcript
(`transcript_path` from hook stdin) and prices the ground-truth `usage`
blocks (input/output/cache tokens, real model string) from assistant
entries — deduplicated by requestId, incrementally via a stored byte
offset, so each invocation reads only the delta. When no transcript is
available it falls back to static per-tool token estimates.

Writes /tmp/claude-cost-{session_id}.json (dir overridable via COST_DIR /
CLAUDE_TMP_DIR) with running totals the /session-cost:report command and
the budget-guard gate read.

Fail-open by design: cost tracking is advisory, so every failure path exits 0
and never blocks the tool pipeline. Writes are atomic with 0600 permissions.
"""
import json
import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cost_tracker import (  # noqa: E402
    atomic_write_json,
    compute_cost,
    estimate_tool_cost,
    resolve_cost_dir,
)

# session_id is spliced into a filename — restrict to the id alphabet.
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,256}")
# Bounded dedup window: one entry per API request, so 200 comfortably covers
# any plausible burst between hook invocations without growing the cost file.
_SEEN_IDS_CAP = 200


def _parse_transcript_delta(path, offset, seen_ids):
    """Price assistant usage blocks appended to the transcript since offset.

    Returns (cost_delta, model, new_offset, seen_ids). Raises OSError when
    the transcript is unreadable (caller falls back to estimates).
    """
    size = os.path.getsize(path)
    if size < offset:  # transcript replaced/truncated (e.g. new file) — restart
        offset = 0
        seen_ids = []

    with open(path, "rb") as f:
        f.seek(offset)
        raw = f.read()
    # A concurrent writer may leave a partial last line — defer it.
    end = raw.rfind(b"\n") + 1
    raw, new_offset = raw[:end], offset + end

    cost = 0.0
    model = None
    seen = list(seen_ids)
    seen_set = set(seen)
    for line in raw.decode("utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant":
            continue
        message = entry.get("message") or {}
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        request_id = entry.get("requestId") or entry.get("request_id")
        if request_id and request_id in seen_set:
            continue
        cost += compute_cost(usage, message.get("model"))
        model = message.get("model") or model
        if request_id:
            seen.append(request_id)
            seen_set.add(request_id)

    return cost, model, new_offset, seen[-_SEEN_IDS_CAP:]


def track(data):
    session_id = data.get("session_id", "")
    if not session_id or not _SESSION_ID_RE.fullmatch(session_id):
        return
    tool_name = data.get("tool_name", "")
    stdin_model = data.get("model", "")  # absent on PostToolUse today; future-proof

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

    delta = None
    transcript_path = data.get("transcript_path")
    if transcript_path:
        try:
            delta, model, new_offset, seen = _parse_transcript_delta(
                transcript_path,
                totals.get("transcript_offset", 0),
                totals.get("seen_request_ids", []),
            )
        except OSError:
            delta = None

    if delta is not None:
        totals["total_estimated_usd"] = totals.get("total_estimated_usd", 0) + delta
        totals["transcript_offset"] = new_offset
        totals["seen_request_ids"] = seen
        totals["basis"] = "transcript"
        if model:
            totals["model"] = model
    elif totals.get("basis") != "transcript":
        # No transcript ever readable for this session: static estimates.
        # Never layer estimate noise on top of recorded actuals.
        totals["total_estimated_usd"] = totals.get(
            "total_estimated_usd", 0
        ) + estimate_tool_cost(tool_name, stdin_model)
        totals["basis"] = "per-tool-estimate"

    totals["tool_calls"] = totals.get("tool_calls", 0) + 1
    totals["last_updated"] = datetime.now(timezone.utc).isoformat()
    totals["model"] = totals.get("model") or stdin_model or "unknown"
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
