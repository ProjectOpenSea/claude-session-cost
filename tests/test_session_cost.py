"""Tests for session-cost.py — the spend reporter behind /session-cost:report."""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location("session_cost", SCRIPTS_DIR / "session-cost.py")
session_cost = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(session_cost)
sys.modules["session_cost"] = session_cost

SCRIPT_PATH = str(SCRIPTS_DIR / "session-cost.py")


@pytest.fixture
def cost_env(tmp_path):
    budgets_path = tmp_path / "budgets.json"
    cost_dir = tmp_path / "costs"
    cost_dir.mkdir()

    class Env:
        def __init__(self):
            self.budgets_path = budgets_path
            self.cost_dir = cost_dir

        def write_budgets(self, config):
            self.budgets_path.write_text(json.dumps(config))

        def write_cost(self, session_id, total_usd, mtime=None, **extra):
            data = {
                "total_estimated_usd": total_usd,
                "tool_calls": 10,
                "model": "claude-opus-4-8",
                "last_updated": "2026-06-10T12:00:00+00:00",
                **extra,
            }
            path = self.cost_dir / f"claude-cost-{session_id}.json"
            path.write_text(json.dumps(data))
            if mtime is not None:
                os.utime(path, (mtime, mtime))
            return path

    return Env()


class TestFindCurrentSession:
    def test_picks_newest_cost_file(self, cost_env):
        now = time.time()
        cost_env.write_cost("old", 1.0, mtime=now - 600)
        cost_env.write_cost("new", 2.0, mtime=now)
        assert session_cost.find_current_session(str(cost_env.cost_dir)) == "new"

    def test_none_when_empty(self, cost_env):
        assert session_cost.find_current_session(str(cost_env.cost_dir)) is None


class TestSessionReport:
    def report(self, cost_env, sid, project=None):
        return session_cost.session_report(
            sid,
            cost_dir=str(cost_env.cost_dir),
            budgets_path=str(cost_env.budgets_path),
            project=project,
        )

    def test_basic_report(self, cost_env):
        cost_env.write_cost("s1", 3.21)
        r = self.report(cost_env, "s1")
        assert r["total_usd"] == 3.21
        assert r["tool_calls"] == 10
        assert "file_mtime" in r

    def test_transcript_basis_rendered(self, cost_env):
        cost_env.write_cost("s1", 3.21, basis="transcript")
        r = self.report(cost_env, "s1")
        assert r["basis"] == "transcript"
        out = session_cost.render_markdown(r)
        assert "actual API usage" in out

    def test_estimate_basis_rendered(self, cost_env):
        cost_env.write_cost("s1", 3.21, basis="per-tool-estimate")
        out = session_cost.render_markdown(self.report(cost_env, "s1"))
        assert "per-tool token averages" in out

    def test_limits_and_percentages(self, cost_env):
        cost_env.write_budgets(
            {"default": {"session_soft_limit_usd": 5.0, "session_hard_limit_usd": 10.0}}
        )
        cost_env.write_cost("s1", 2.5)
        r = self.report(cost_env, "s1")
        assert r["pct_of_soft"] == 50.0
        assert r["pct_of_hard"] == 25.0

    def test_zero_limits_kept_no_percentage(self, cost_env):
        cost_env.write_budgets(
            {"default": {"session_soft_limit_usd": 0, "session_hard_limit_usd": 0}}
        )
        cost_env.write_cost("s1", 2.5)
        r = self.report(cost_env, "s1")
        assert r["limits"]["session_soft_limit_usd"] == 0
        assert "pct_of_soft" not in r

    def test_project_override(self, cost_env):
        cost_env.write_budgets(
            {
                "default": {"session_soft_limit_usd": 5.0},
                "projects": {"myproj": {"session_soft_limit_usd": 20.0}},
            }
        )
        cost_env.write_cost("s1", 2.0)
        r = self.report(cost_env, "s1", project="myproj")
        assert r["limits"]["session_soft_limit_usd"] == 20.0

    def test_team_total(self, cost_env):
        cost_env.write_cost("s1", 2.0, team_name="alpha")
        cost_env.write_cost("s2", 3.0, team_name="alpha")
        cost_env.write_cost("s3", 9.0, team_name="other")
        r = self.report(cost_env, "s1")
        assert r["team"] == {"name": "alpha", "total_usd": 5.0}

    def test_missing_or_malformed_returns_none(self, cost_env):
        assert self.report(cost_env, "nope") is None
        (cost_env.cost_dir / "claude-cost-bad.json").write_text("{x")
        assert self.report(cost_env, "bad") is None

    def test_malformed_budgets_still_reports(self, cost_env):
        cost_env.budgets_path.write_text("{broken")
        cost_env.write_cost("s1", 1.5)
        r = self.report(cost_env, "s1")
        assert r["total_usd"] == 1.5
        assert r["limits"] == {}


class TestRenderMarkdown:
    def test_render_spend_limits_mtime(self, cost_env):
        cost_env.write_budgets(
            {"default": {"session_soft_limit_usd": 5.0, "session_hard_limit_usd": 10.0}}
        )
        cost_env.write_cost("s1", 2.5)
        r = session_cost.session_report(
            "s1", cost_dir=str(cost_env.cost_dir), budgets_path=str(cost_env.budgets_path)
        )
        out = session_cost.render_markdown(r)
        assert "$2.50" in out
        assert "$5.00" in out
        assert r["file_mtime"] in out


class TestCLI:
    def run_cli(self, cost_env, *args):
        env = dict(
            os.environ,
            COST_DIR=str(cost_env.cost_dir),
            BUDGET_CONFIG_PATH=str(cost_env.budgets_path),
        )
        return subprocess.run(
            [sys.executable, SCRIPT_PATH, *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
        )

    def test_defaults_to_newest(self, cost_env):
        now = time.time()
        cost_env.write_cost("old", 1.0, mtime=now - 600)
        cost_env.write_cost("new", 2.0, mtime=now)
        result = self.run_cli(cost_env)
        assert result.returncode == 0
        assert "new" in result.stdout

    def test_single_arg_tail_is_shell_split(self, cost_env):
        now = time.time()
        cost_env.write_cost("a", 1.0, mtime=now - 60)
        cost_env.write_cost("b", 2.0, mtime=now)
        result = self.run_cli(cost_env, "--all --json")
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert [r["session_id"] for r in data] == ["b", "a"]

    def test_empty_tail_arg_ok(self, cost_env):
        cost_env.write_cost("s1", 1.0)
        result = self.run_cli(cost_env, "")
        assert result.returncode == 0

    def test_json_output(self, cost_env):
        cost_env.write_cost("s1", 2.0)
        result = self.run_cli(cost_env, "--json")
        assert json.loads(result.stdout)["session_id"] == "s1"

    def test_all_table(self, cost_env):
        now = time.time()
        cost_env.write_cost("sess-older", 1.0, mtime=now - 60)
        cost_env.write_cost("sess-newer", 2.0, mtime=now)
        result = self.run_cli(cost_env, "--all")
        assert result.stdout.index("sess-newer") < result.stdout.index("sess-older")

    def test_no_files_exits_1(self, cost_env):
        result = self.run_cli(cost_env)
        assert result.returncode == 1

    @pytest.mark.parametrize(
        "evil", ["../../../etc/passwd", "x; touch /tmp/pwned", "$(boom)"]
    )
    def test_rejects_metachar_session_ids(self, cost_env, evil):
        cost_env.write_cost("s1", 1.0)
        result = self.run_cli(cost_env, evil)
        assert result.returncode == 1
        assert "Invalid session id" in result.stderr
