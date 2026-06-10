"""Structural checks on the plugin manifests and component wiring."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(rel):
    return json.loads((ROOT / rel).read_text())


class TestPluginJson:
    def test_parses_and_has_required_fields(self):
        manifest = load(".claude-plugin/plugin.json")
        assert manifest["name"] == "session-cost"
        assert re.fullmatch(r"[a-z0-9-]+", manifest["name"])
        assert manifest["version"]
        assert manifest["license"] == "MIT"

    def test_hooks_path_exists(self):
        manifest = load(".claude-plugin/plugin.json")
        hooks_rel = manifest.get("hooks", "./hooks/hooks.json")
        assert (ROOT / hooks_rel).exists()


class TestHooksJson:
    def test_posttooluse_wired_to_plugin_root_script(self):
        hooks = load("hooks/hooks.json")
        post = hooks["hooks"]["PostToolUse"]
        commands = [h["command"] for entry in post for h in entry["hooks"]]
        assert any("${CLAUDE_PLUGIN_ROOT}" in c and "cost-track.py" in c for c in commands)

    def test_referenced_scripts_exist(self):
        hooks = load("hooks/hooks.json")
        for entries in hooks["hooks"].values():
            for entry in entries:
                for h in entry["hooks"]:
                    m = re.search(r"\$\{CLAUDE_PLUGIN_ROOT\}\"?/(\S+\.py)", h["command"])
                    assert m, f"no plugin-root script in: {h['command']}"
                    assert (ROOT / m.group(1)).exists()


class TestMarketplaceJson:
    def test_self_marketplace_points_at_repo_root(self):
        mp = load(".claude-plugin/marketplace.json")
        assert mp["name"]
        assert mp["owner"]["name"]
        entry = next(p for p in mp["plugins"] if p["name"] == "session-cost")
        assert entry["source"] in ("./", ".")


class TestCommand:
    def test_report_command_exists_and_quotes_arguments(self):
        body = (ROOT / "commands" / "report.md").read_text()
        assert "${CLAUDE_PLUGIN_ROOT}" in body
        assert '"$ARGUMENTS"' in body
        # the unquoted form must not appear anywhere
        assert not re.search(r'(?<!")\$ARGUMENTS(?!")', body)
