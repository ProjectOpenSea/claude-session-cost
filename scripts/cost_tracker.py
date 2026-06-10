#!/usr/bin/env python3
"""Shared pricing constants and cost estimation for the session-cost plugin.

Stdlib-only. Used by:
- cost-track.py  (PostToolUse hook — accumulates per-tool-call estimates)
- session-cost.py (the /session-cost:report command backend)
"""
import json
import os

# --- Pricing (USD per million tokens) ---
# Source: Anthropic published pricing, 2026. Update when new models ship.
PRICING = {
    "claude-fable-5":    {"input": 10.00, "output": 50.00},
    "claude-mythos-5":   {"input": 10.00, "output": 50.00},
    "claude-opus-4-8":   {"input":  5.00, "output": 25.00},
    "claude-opus-4-7":   {"input":  5.00, "output": 25.00},
    "claude-opus-4-6":   {"input":  5.00, "output": 25.00},
    "claude-opus-4-5":   {"input":  5.00, "output": 25.00},
    "claude-sonnet-4-6": {"input":  3.00, "output": 15.00},
    "claude-sonnet-4-5": {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5":  {"input":  1.00, "output":  5.00},
    # PostToolUse hook input carries no model field, so most estimates use
    # this default. Opus-tier pricing — the common case for Claude Code.
    "default":           {"input":  5.00, "output": 25.00},
}

# Cache multipliers (relative to base input price)
CACHE_READ_DISCOUNT = 0.10        # cache reads cost 10% of input price
CACHE_CREATION_PREMIUM = 1.25     # 5-minute-TTL cache writes
CACHE_CREATION_PREMIUM_1H = 2.0   # 1-hour-TTL cache writes (what Claude Code uses)

# Average token estimates per tool type (input_tokens, output_tokens).
# Rough averages — the value is in aggregation, not per-call precision.
_TOOL_TOKEN_ESTIMATES = {
    "Edit":        (2000, 500),
    "Write":       (2000, 500),
    "Read":        (1000, 200),
    "Bash":        (1000, 500),
    "Grep":        (800,  300),
    "Glob":        (500,  200),
    "Agent":       (5000, 2000),
    "TaskCreate":  (1000, 300),
    "TaskUpdate":  (500,  200),
    "SendMessage": (800,  300),
}
_DEFAULT_TOKEN_ESTIMATE = (1000, 300)


def resolve_cost_dir():
    """Directory holding claude-cost-{session_id}.json files.

    COST_DIR → CLAUDE_TMP_DIR → /tmp. The default is a fixed path (not
    tempfile.gettempdir()) because the writer (hook process) and reader
    (the command's sandboxed Bash) may see different $TMPDIR values; both
    must agree on one location.
    """
    return os.environ.get("COST_DIR") or os.environ.get("CLAUDE_TMP_DIR") or "/tmp"


def atomic_write_json(path, data):
    """Atomically replace path with data as JSON, 0600. Raises OSError.

    Deliberately avoids the tempfile module: this runs on the PostToolUse
    hot path and tempfile's transitive imports add ~9ms per hook invocation.
    O_EXCL + a pid-suffixed name gives the same single-writer safety.
    """
    tmp_path = f"{path}.tmp.{os.getpid()}"
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "w") as wf:
            json.dump(data, wf)
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_pricing(model):
    """Return pricing dict for a model, with prefix matching fallback."""
    if not model:
        return PRICING["default"]
    if model in PRICING:
        return PRICING[model]
    for key in PRICING:
        if key != "default" and model.startswith(key):
            return PRICING[key]
    return PRICING["default"]


def compute_cost(usage, model):
    """Estimated USD cost from a Messages API usage dict.

    When the usage carries a per-TTL `cache_creation` breakdown (real Claude
    Code transcripts do, and use 1-hour cache exclusively), price 5m writes
    at 1.25x and 1h writes at 2x base input. Otherwise fall back to 1.25x on
    the `cache_creation_input_tokens` total.
    """
    pricing = get_pricing(model)
    breakdown = usage.get("cache_creation")
    if isinstance(breakdown, dict):
        cache_write_cost = pricing["input"] * (
            breakdown.get("ephemeral_5m_input_tokens", 0) * CACHE_CREATION_PREMIUM
            + breakdown.get("ephemeral_1h_input_tokens", 0) * CACHE_CREATION_PREMIUM_1H
        )
    else:
        cache_write_cost = (
            usage.get("cache_creation_input_tokens", 0)
            * pricing["input"] * CACHE_CREATION_PREMIUM
        )
    return (
        usage.get("input_tokens", 0) * pricing["input"]
        + usage.get("output_tokens", 0) * pricing["output"]
        + cache_write_cost
        + usage.get("cache_read_input_tokens", 0)
        * pricing["input"] * CACHE_READ_DISCOUNT
    ) / 1_000_000


def estimate_tool_cost(tool_name, model=None):
    """Estimated USD cost for one tool call from per-tool token averages."""
    input_tokens, output_tokens = _TOOL_TOKEN_ESTIMATES.get(
        tool_name, _DEFAULT_TOKEN_ESTIMATE
    )
    pricing = get_pricing(model)
    return (
        input_tokens * pricing["input"] + output_tokens * pricing["output"]
    ) / 1_000_000
