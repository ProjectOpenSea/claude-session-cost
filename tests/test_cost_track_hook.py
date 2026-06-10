"""Tests for cost-track.py — the PostToolUse hook that writes cost files."""
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "cost-track.py")


def run_hook(cost_dir, payload):
    env = dict(os.environ, COST_DIR=str(cost_dir))
    return subprocess.run(
        [sys.executable, SCRIPT],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def hook_input(session_id="abc-123", tool_name="Edit"):
    return {
        "session_id": session_id,
        "hook_event_name": "PostToolUse",
        "tool_name": tool_name,
        "tool_input": {},
        "cwd": "/tmp",
    }


class TestWriting:
    def test_creates_cost_file(self, tmp_path):
        result = run_hook(tmp_path, hook_input())
        assert result.returncode == 0
        data = json.loads((tmp_path / "claude-cost-abc-123.json").read_text())
        assert data["total_estimated_usd"] > 0
        assert data["tool_calls"] == 1
        assert "last_updated" in data

    def test_accumulates_across_calls(self, tmp_path):
        run_hook(tmp_path, hook_input())
        run_hook(tmp_path, hook_input(tool_name="Bash"))
        data = json.loads((tmp_path / "claude-cost-abc-123.json").read_text())
        assert data["tool_calls"] == 2

    def test_file_mode_is_0600(self, tmp_path):
        run_hook(tmp_path, hook_input())
        mode = stat.S_IMODE(os.stat(tmp_path / "claude-cost-abc-123.json").st_mode)
        assert mode == 0o600

    def test_team_name_env_recorded(self, tmp_path):
        env = dict(os.environ, COST_DIR=str(tmp_path), CLAUDE_TEAM_NAME="alpha")
        subprocess.run(
            [sys.executable, SCRIPT],
            input=json.dumps(hook_input()),
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )
        data = json.loads((tmp_path / "claude-cost-abc-123.json").read_text())
        assert data["team_name"] == "alpha"


class TestFailOpen:
    """The hook must NEVER block the pipeline — exit 0 on every failure."""

    def test_malformed_stdin_exits_zero(self, tmp_path):
        result = run_hook(tmp_path, "{not json")
        assert result.returncode == 0

    def test_empty_stdin_exits_zero(self, tmp_path):
        result = run_hook(tmp_path, "")
        assert result.returncode == 0

    def test_missing_session_id_exits_zero_writes_nothing(self, tmp_path):
        payload = hook_input()
        del payload["session_id"]
        result = run_hook(tmp_path, payload)
        assert result.returncode == 0
        assert list(tmp_path.glob("claude-cost-*")) == []

    @pytest.mark.parametrize(
        "evil", ["../../../etc/cron.d/x", "a/b", "x;rm -rf /", "$(boom)"]
    )
    def test_hostile_session_id_writes_nothing(self, tmp_path, evil):
        result = run_hook(tmp_path, hook_input(session_id=evil))
        assert result.returncode == 0
        assert list(tmp_path.rglob("*")) == []

    def test_unwritable_cost_dir_exits_zero(self, tmp_path):
        target = tmp_path / "nope"
        target.mkdir()
        target.chmod(0o500)
        try:
            result = run_hook(target, hook_input())
            assert result.returncode == 0
        finally:
            target.chmod(0o700)

    def test_corrupt_existing_file_is_replaced_not_crashed(self, tmp_path):
        (tmp_path / "claude-cost-abc-123.json").write_text("{corrupt")
        result = run_hook(tmp_path, hook_input())
        assert result.returncode == 0
        data = json.loads((tmp_path / "claude-cost-abc-123.json").read_text())
        assert data["tool_calls"] == 1
