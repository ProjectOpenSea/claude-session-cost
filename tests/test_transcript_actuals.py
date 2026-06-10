"""Tests for transcript-based actual-usage cost tracking in cost-track.py.

The PostToolUse stdin includes transcript_path; assistant entries there carry
ground-truth usage blocks (input/output/cache tokens) and the real model.
The hook prices those actuals and only falls back to per-tool estimates when
no transcript is available.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location("cost_track", SCRIPTS_DIR / "cost-track.py")
cost_track = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cost_track)
sys.modules["cost_track"] = cost_track

sys.path.insert(0, str(SCRIPTS_DIR))
from cost_tracker import compute_cost  # noqa: E402

SCRIPT = str(SCRIPTS_DIR / "cost-track.py")


def assistant_line(request_id, model="claude-sonnet-4-6", **usage):
    return json.dumps(
        {
            "type": "assistant",
            "requestId": request_id,
            "message": {"model": model, "usage": usage},
        }
    )


def run_hook(cost_dir, transcript_path, session_id="sess-1", tool_name="Edit"):
    payload = {
        "session_id": session_id,
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
    }
    if transcript_path is not None:
        payload["transcript_path"] = str(transcript_path)
    env = dict(os.environ, COST_DIR=str(cost_dir))
    return subprocess.run(
        [sys.executable, SCRIPT],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def read_cost(cost_dir, session_id="sess-1"):
    return json.loads((Path(cost_dir) / f"claude-cost-{session_id}.json").read_text())


class TestActuals:
    def test_prices_real_usage_with_real_model(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        usage = dict(
            input_tokens=10_000,
            output_tokens=2_000,
            cache_read_input_tokens=300_000,
            cache_creation_input_tokens=50_000,
        )
        transcript.write_text(assistant_line("req-1", **usage) + "\n")
        run_hook(tmp_path, transcript)
        data = read_cost(tmp_path)
        assert abs(data["total_estimated_usd"] - compute_cost(usage, "claude-sonnet-4-6")) < 1e-9
        assert data["model"] == "claude-sonnet-4-6"
        assert data["basis"] == "transcript"

    def test_duplicate_request_ids_counted_once(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        line = assistant_line("req-1", input_tokens=1000, output_tokens=100)
        transcript.write_text(line + "\n" + line + "\n")
        run_hook(tmp_path, transcript)
        expected = compute_cost(
            {"input_tokens": 1000, "output_tokens": 100}, "claude-sonnet-4-6"
        )
        assert abs(read_cost(tmp_path)["total_estimated_usd"] - expected) < 1e-9

    def test_incremental_only_prices_new_lines(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(assistant_line("req-1", input_tokens=1000, output_tokens=100) + "\n")
        run_hook(tmp_path, transcript)
        first_total = read_cost(tmp_path)["total_estimated_usd"]

        with open(transcript, "a") as f:
            f.write(assistant_line("req-2", input_tokens=1000, output_tokens=100) + "\n")
        run_hook(tmp_path, transcript)
        data = read_cost(tmp_path)
        assert abs(data["total_estimated_usd"] - 2 * first_total) < 1e-9
        assert data["tool_calls"] == 2

    def test_no_new_usage_adds_nothing(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(assistant_line("req-1", input_tokens=1000, output_tokens=100) + "\n")
        run_hook(tmp_path, transcript)
        total = read_cost(tmp_path)["total_estimated_usd"]
        run_hook(tmp_path, transcript)  # no transcript growth
        assert read_cost(tmp_path)["total_estimated_usd"] == total

    def test_non_dict_message_line_skipped_not_fatal(self, tmp_path):
        """A truthy non-dict message must skip the line, not abort the whole
        delta (an AttributeError would silently lose the good lines too)."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            json.dumps({"type": "assistant", "requestId": "bad", "message": "oops"})
            + "\n"
            + assistant_line("req-1", input_tokens=1000, output_tokens=100)
            + "\n"
        )
        result = run_hook(tmp_path, transcript)
        assert result.returncode == 0
        data = read_cost(tmp_path)
        assert data["basis"] == "transcript"
        expected = compute_cost(
            {"input_tokens": 1000, "output_tokens": 100}, "claude-sonnet-4-6"
        )
        assert abs(data["total_estimated_usd"] - expected) < 1e-9

    def test_malformed_and_non_assistant_lines_skipped(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            "{not json\n"
            + json.dumps({"type": "user", "message": {}})
            + "\n"
            + assistant_line("req-1", input_tokens=1000, output_tokens=100)
            + "\n"
        )
        result = run_hook(tmp_path, transcript)
        assert result.returncode == 0
        expected = compute_cost(
            {"input_tokens": 1000, "output_tokens": 100}, "claude-sonnet-4-6"
        )
        assert abs(read_cost(tmp_path)["total_estimated_usd"] - expected) < 1e-9

    def test_truncated_transcript_resets_offset(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(
            assistant_line("req-1", input_tokens=1000, output_tokens=100) + "\n"
        )
        run_hook(tmp_path, transcript)
        # transcript replaced by a shorter file (e.g. new transcript after compaction)
        transcript.write_text(
            assistant_line("req-9", input_tokens=500, output_tokens=50) + "\n"
        )
        result = run_hook(tmp_path, transcript)
        assert result.returncode == 0
        data = read_cost(tmp_path)
        expected = compute_cost(
            {"input_tokens": 1000, "output_tokens": 100}, "claude-sonnet-4-6"
        ) + compute_cost({"input_tokens": 500, "output_tokens": 50}, "claude-sonnet-4-6")
        assert abs(data["total_estimated_usd"] - expected) < 1e-9

    def test_seen_request_ids_capped(self, tmp_path):
        transcript = tmp_path / "t.jsonl"
        lines = [
            assistant_line(f"req-{i}", input_tokens=10, output_tokens=1) for i in range(300)
        ]
        transcript.write_text("\n".join(lines) + "\n")
        run_hook(tmp_path, transcript)
        data = read_cost(tmp_path)
        assert len(data["seen_request_ids"]) <= 200


class TestFallback:
    def test_no_transcript_path_falls_back_to_estimate(self, tmp_path):
        run_hook(tmp_path, None)
        data = read_cost(tmp_path)
        assert data["total_estimated_usd"] > 0
        assert data["basis"] == "per-tool-estimate"

    def test_unreadable_transcript_falls_back_to_estimate(self, tmp_path):
        result = run_hook(tmp_path, tmp_path / "missing.jsonl")
        assert result.returncode == 0
        data = read_cost(tmp_path)
        assert data["total_estimated_usd"] > 0
        assert data["basis"] == "per-tool-estimate"

    def test_fallback_does_not_mix_with_actuals(self, tmp_path):
        """Once actuals have been recorded, an unreadable transcript on a later
        call must not add estimate noise on top of them."""
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(assistant_line("req-1", input_tokens=1000, output_tokens=100) + "\n")
        run_hook(tmp_path, transcript)
        total = read_cost(tmp_path)["total_estimated_usd"]
        run_hook(tmp_path, tmp_path / "gone.jsonl")
        data = read_cost(tmp_path)
        assert data["total_estimated_usd"] == total
        assert data["tool_calls"] == 2
