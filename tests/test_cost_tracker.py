"""Tests for cost_tracker.py — pricing table and estimation."""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cost_tracker


class TestPricing:
    def test_known_models_present(self):
        for model in (
            "claude-fable-5",
            "claude-mythos-5",
            "claude-opus-4-8",
            "claude-sonnet-4-6",
            "claude-haiku-4-5",
        ):
            assert model in cost_tracker.PRICING

    def test_mythos_priced_at_fable_tier(self):
        p = cost_tracker.get_pricing("claude-mythos-5")
        assert p["input"] == 10.00
        assert p["output"] == 50.00

    def test_get_pricing_exact_match(self):
        p = cost_tracker.get_pricing("claude-opus-4-8")
        assert p["input"] == 5.00
        assert p["output"] == 25.00

    def test_get_pricing_prefix_match(self):
        p = cost_tracker.get_pricing("claude-haiku-4-5-20251001")
        assert p == cost_tracker.PRICING["claude-haiku-4-5"]

    def test_get_pricing_unknown_falls_back_to_default(self):
        assert cost_tracker.get_pricing("unknown") == cost_tracker.PRICING["default"]
        assert cost_tracker.get_pricing("") == cost_tracker.PRICING["default"]
        assert cost_tracker.get_pricing(None) == cost_tracker.PRICING["default"]


class TestComputeCost:
    def test_basic_io_tokens(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        cost = cost_tracker.compute_cost(usage, "claude-opus-4-8")
        assert cost == 5.00 + 25.00

    def test_cache_multipliers_without_ttl_breakdown(self):
        usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 1_000_000,
            "cache_read_input_tokens": 1_000_000,
        }
        cost = cost_tracker.compute_cost(usage, "claude-opus-4-8")
        assert cost == 5.00 * 1.25 + 5.00 * 0.10

    def test_cache_ttl_breakdown_prices_1h_writes_at_2x(self):
        """Real Claude Code transcripts carry a cache_creation breakdown and
        use 1-hour cache exclusively — 1h writes cost 2x base, not 1.25x."""
        usage = {
            "cache_creation_input_tokens": 1_500_000,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 500_000,
                "ephemeral_1h_input_tokens": 1_000_000,
            },
        }
        cost = cost_tracker.compute_cost(usage, "claude-opus-4-8")
        # breakdown replaces the flat 1.25x on the total — no double count
        assert cost == 0.5 * 5.00 * 1.25 + 1.0 * 5.00 * 2.0

    def test_cache_ttl_breakdown_real_session_shape(self):
        """The exact shape observed in a live CC transcript: all 1h, 5m zero."""
        usage = {
            "input_tokens": 18,
            "output_tokens": 160,
            "cache_read_input_tokens": 31_312,
            "cache_creation_input_tokens": 31_463,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 0,
                "ephemeral_1h_input_tokens": 31_463,
            },
        }
        cost = cost_tracker.compute_cost(usage, "claude-haiku-4-5")
        expected = (
            18 * 1.00 + 160 * 5.00 + 31_312 * 1.00 * 0.10 + 31_463 * 1.00 * 2.0
        ) / 1e6
        assert abs(cost - expected) < 1e-12

    def test_cache_breakdown_non_dict_falls_back_to_flat(self):
        usage = {"cache_creation_input_tokens": 1_000_000, "cache_creation": "junk"}
        cost = cost_tracker.compute_cost(usage, "claude-opus-4-8")
        assert cost == 5.00 * 1.25

    def test_empty_usage_is_zero(self):
        assert cost_tracker.compute_cost({}, "claude-opus-4-8") == 0.0


class TestEstimateToolCost:
    def test_known_tool_positive(self):
        assert cost_tracker.estimate_tool_cost("Edit", "claude-opus-4-8") > 0

    def test_unknown_tool_uses_default_estimate(self):
        cost = cost_tracker.estimate_tool_cost("SomeNewTool", "claude-opus-4-8")
        assert cost > 0

    def test_unknown_model_uses_default_pricing(self):
        cost = cost_tracker.estimate_tool_cost("Edit", "")
        default = cost_tracker.PRICING["default"]
        expected = (2000 * default["input"] + 500 * default["output"]) / 1_000_000
        assert abs(cost - expected) < 1e-9


class TestAtomicWrite:
    def test_writes_json_with_0600(self, tmp_path):
        import stat

        target = tmp_path / "out.json"
        cost_tracker.atomic_write_json(str(target), {"k": 1})
        assert json.loads(target.read_text()) == {"k": 1}
        assert stat.S_IMODE(os.stat(target).st_mode) == 0o600

    def test_overwrites_existing(self, tmp_path):
        target = tmp_path / "out.json"
        target.write_text("{}")
        cost_tracker.atomic_write_json(str(target), {"k": 2})
        assert json.loads(target.read_text())["k"] == 2

    def test_hot_path_does_not_import_tempfile(self):
        """tempfile's transitive imports add ~9ms per hook call — keep it off
        the PostToolUse/PreToolUse hot path."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import cost_tracker; print('tempfile' in sys.modules)",
                str(SCRIPTS_DIR),
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.stdout.strip() == "False", result.stderr


class TestResolveCostDir:
    def test_cost_dir_env_wins(self, monkeypatch):
        monkeypatch.setenv("COST_DIR", "/custom")
        monkeypatch.setenv("CLAUDE_TMP_DIR", "/other")
        assert cost_tracker.resolve_cost_dir() == "/custom"

    def test_claude_tmp_dir_fallback(self, monkeypatch):
        monkeypatch.delenv("COST_DIR", raising=False)
        monkeypatch.setenv("CLAUDE_TMP_DIR", "/claude-tmp")
        assert cost_tracker.resolve_cost_dir() == "/claude-tmp"

    def test_default_is_tmp(self, monkeypatch):
        monkeypatch.delenv("COST_DIR", raising=False)
        monkeypatch.delenv("CLAUDE_TMP_DIR", raising=False)
        assert cost_tracker.resolve_cost_dir() == "/tmp"
