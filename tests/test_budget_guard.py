"""Tests for budget-guard.py — the PreToolUse enforcement hook."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = str(Path(__file__).resolve().parent.parent / "scripts" / "budget-guard.py")


@pytest.fixture
def env(tmp_path):
    budgets = tmp_path / "budgets.json"
    cost_dir = tmp_path / "costs"
    cost_dir.mkdir()

    class Env:
        budgets_path = budgets
        costs = cost_dir

        def write_budgets(self, config):
            budgets.write_text(json.dumps(config))

        def write_cost(self, session_id, total_usd, **extra):
            (cost_dir / f"claude-cost-{session_id}.json").write_text(
                json.dumps({"total_estimated_usd": total_usd, "tool_calls": 5, **extra})
            )

        def read_cost(self, session_id):
            return json.loads((cost_dir / f"claude-cost-{session_id}.json").read_text())

        def run(self, payload, **env_extra):
            run_env = dict(
                os.environ,
                COST_DIR=str(cost_dir),
                BUDGET_CONFIG_PATH=str(budgets),
            )
            run_env.pop("CLAUDE_TEAM_NAME", None)
            run_env.update(env_extra)
            return subprocess.run(
                [sys.executable, SCRIPT],
                input=payload if isinstance(payload, str) else json.dumps(payload),
                capture_output=True,
                text=True,
                env=run_env,
                timeout=15,
            )

    return Env()


def hook_input(session_id="sess-1"):
    return {"session_id": session_id, "hook_event_name": "PreToolUse", "tool_name": "Edit"}


class TestNoop:
    def test_allows_when_no_budgets_file(self, env):
        env.write_cost("sess-1", 100.0)
        r = env.run(hook_input())
        assert r.returncode == 0
        assert json.loads(r.stdout or "{}") == {}

    def test_allows_when_no_cost_file(self, env):
        env.write_budgets({"default": {"session_hard_limit_usd": 1.0}})
        r = env.run(hook_input())
        assert r.returncode == 0

    def test_allows_under_soft_limit(self, env):
        env.write_budgets({"default": {"session_soft_limit_usd": 5.0}})
        env.write_cost("sess-1", 1.0)
        r = env.run(hook_input())
        assert r.returncode == 0
        assert json.loads(r.stdout or "{}") == {}


class TestSoftLimit:
    def test_warns_at_soft_limit(self, env):
        env.write_budgets({"default": {"session_soft_limit_usd": 5.0}})
        env.write_cost("sess-1", 6.0)
        r = env.run(hook_input())
        assert r.returncode == 0
        out = json.loads(r.stdout)
        assert "systemMessage" in out
        assert "$6.00" in out["systemMessage"]

    def test_warning_deduplicated_per_dollar(self, env):
        env.write_budgets({"default": {"session_soft_limit_usd": 5.0}})
        env.write_cost("sess-1", 6.0)
        assert "systemMessage" in json.loads(env.run(hook_input()).stdout)
        # same spend again — deduped
        assert json.loads(env.run(hook_input()).stdout or "{}") == {}
        # +$1 — warns again
        env.write_cost(
            "sess-1", 7.5, last_budget_warn_usd=env.read_cost("sess-1")["last_budget_warn_usd"]
        )
        assert "systemMessage" in json.loads(env.run(hook_input()).stdout)


class TestHardLimit:
    def test_blocks_at_hard_limit_exit_2(self, env):
        env.write_budgets({"default": {"session_hard_limit_usd": 5.0}})
        env.write_cost("sess-1", 5.0)
        r = env.run(hook_input())
        assert r.returncode == 2
        assert "BUDGET EXCEEDED" in r.stderr

    def test_zero_hard_limit_blocks_everything(self, env):
        """A limit of 0 is a valid hard stop — must not be dropped by truthiness."""
        env.write_budgets({"default": {"session_hard_limit_usd": 0}})
        env.write_cost("sess-1", 0.01)
        r = env.run(hook_input())
        assert r.returncode == 2

    def test_project_override_raises_limit(self, env):
        env.write_budgets(
            {
                "default": {"session_hard_limit_usd": 1.0},
                "projects": {"myproj": {"session_hard_limit_usd": 50.0}},
            }
        )
        env.write_cost("sess-1", 5.0)
        payload = hook_input()
        payload["cwd"] = "/home/u/code/myproj/src"
        r = env.run(payload)
        assert r.returncode == 0


class TestTeamLimits:
    def test_team_hard_limit_blocks(self, env):
        env.write_budgets({"default": {"team_hard_limit_usd": 5.0}})
        env.write_cost("sess-1", 3.0, team_name="alpha")
        env.write_cost("sess-2", 3.0, team_name="alpha")
        r = env.run(hook_input(), CLAUDE_TEAM_NAME="alpha")
        assert r.returncode == 2
        assert "alpha" in r.stderr


class TestFailOpen:
    def test_malformed_stdin_allows(self, env):
        r = env.run("{nope")
        assert r.returncode == 0

    def test_missing_session_id_allows(self, env):
        env.write_budgets({"default": {"session_hard_limit_usd": 0}})
        r = env.run({"hook_event_name": "PreToolUse"})
        assert r.returncode == 0

    def test_hostile_session_id_allows_without_path_touch(self, env):
        env.write_budgets({"default": {"session_hard_limit_usd": 0}})
        r = env.run(hook_input(session_id="../../etc/passwd"))
        assert r.returncode == 0

    def test_corrupt_budgets_allows(self, env):
        env.budgets_path.write_text("{broken")
        env.write_cost("sess-1", 100.0)
        r = env.run(hook_input())
        assert r.returncode == 0
