#!/usr/bin/env python3
"""Shared pricing constants and cost estimation for the session-cost plugin.

Stdlib-only. Used by:
- cost-track.py  (PostToolUse hook — accumulates per-tool-call estimates)
- session-cost.py (the /session-cost:report command backend)
"""
import os

# --- Pricing (USD per million tokens) ---
# Source: Anthropic published pricing, 2026. Update when new models ship.
PRICING = {
    "claude-fable-5":    {"input": 10.00, "output": 50.00},
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

# Cache multipliers (5-minute TTL writes)
CACHE_READ_DISCOUNT = 0.10     # cache reads cost ~10% of input price
CACHE_CREATION_PREMIUM = 1.25  # cache writes cost ~125% of input price

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
    """Estimated USD cost from a Messages API usage dict."""
    pricing = get_pricing(model)
    return (
        usage.get("input_tokens", 0) * pricing["input"]
        + usage.get("output_tokens", 0) * pricing["output"]
        + usage.get("cache_creation_input_tokens", 0)
        * pricing["input"] * CACHE_CREATION_PREMIUM
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
