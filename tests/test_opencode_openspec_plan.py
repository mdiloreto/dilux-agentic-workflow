"""OpenCode must allow only OpenSpec planning documents in opted-in PLAN."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path):
    shutil.copytree(ROOT / "ddw", tmp_path / ".ddw")
    (tmp_path / "AGENTS.md").write_text(
        "## Specification framework\n\nSpec format: OpenSpec\n", encoding="utf-8"
    )
    (tmp_path / ".ddw-state.json").write_text(
        json.dumps({
            "phase": "PLAN",
            "tier": "FEATURE",
            "ticket": "T-1",
            "title": "OpenSpec plan",
            "autonomy": "autonomous",
            "gates": {"define": True},
            "history": [
                {"from": "IDLE", "to": "CLASSIFY", "action": "classify"},
                {"from": "CLASSIFY", "to": "DEFINE", "action": "feature"},
                {"from": "DEFINE", "to": "PLAN", "action": "validated PRD"},
            ],
        }),
        encoding="utf-8",
    )
    return tmp_path


def call_patch_hook(project: Path, paths: list[str]) -> subprocess.CompletedProcess[str]:
    patch = "*** Begin Patch\n" + "".join(
        f"*** Add File: {path}\n+# Planned document\n" for path in paths
    ) + "*** End Patch\n"
    program = """
const {default: plugin} = await import(process.argv[1]);
const hooks = {};
const dispose = await plugin.setup({
  location: {directory: process.argv[2]},
  tool: {hook: async (name, callback) => { hooks[name] = callback; }},
  session: {hook: async () => {}},
  event: {subscribe: async function* () {}},
});
try {
  await hooks["execute.before"]({tool: "patch", input: {patchText: process.argv[3]}});
  console.log("ALLOW");
} catch (error) {
  console.log(error.message);
  process.exitCode = 2;
} finally {
  dispose();
}
"""
    adapter = ROOT / "adapters/opencode/plugin/ddw.js"
    return subprocess.run(
        ["node", "--input-type=module", "-e", program, adapter.as_uri(), str(project), patch],
        text=True,
        capture_output=True,
        check=False,
    )


def test_plan_allows_openspec_proposal_with_opt_in(project):
    result = call_patch_hook(project, ["openspec/changes/add-ledger/proposal.md"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ALLOW"
