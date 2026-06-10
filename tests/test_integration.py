"""End-to-end: the hook writes cost files, the reporter reads them back."""
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def run(script, cost_dir, *args, stdin=None):
    env = dict(os.environ, COST_DIR=str(cost_dir))
    env.pop("CLAUDE_TEAM_NAME", None)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def test_hook_then_report_roundtrip(tmp_path):
    for tool in ("Edit", "Bash", "Read"):
        payload = json.dumps(
            {"session_id": "smoke-1", "tool_name": tool, "hook_event_name": "PostToolUse"}
        )
        result = run("cost-track.py", tmp_path, stdin=payload)
        assert result.returncode == 0, result.stderr

    # Empty-string arg mirrors the command's "$ARGUMENTS" with no tail.
    report = run("session-cost.py", tmp_path, "")
    assert report.returncode == 0, report.stderr
    assert "smoke-1" in report.stdout
    assert "Tool calls tracked**: 3" in report.stdout

    as_json = run("session-cost.py", tmp_path, "--json")
    data = json.loads(as_json.stdout)
    assert data["tool_calls"] == 3
    # Edit(2000,500) + Bash(1000,500) + Read(1000,200) at default $5/$25 per MTok
    expected = (2000 * 5 + 500 * 25 + 1000 * 5 + 500 * 25 + 1000 * 5 + 200 * 25) / 1e6
    assert abs(data["total_usd"] - expected) < 1e-9
