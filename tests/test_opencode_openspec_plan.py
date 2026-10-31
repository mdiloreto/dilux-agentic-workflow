"""OpenCode V2 must complete opted-in OpenSpec planning without weakening guards."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "ddw" / "scripts"))
import ddw_receipt


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "ddw", tmp_path / ".ddw")
    (tmp_path / "AGENTS.md").write_text(
        "## Specification framework\n\nSpec format: OpenSpec\n", encoding="utf-8"
    )
    (tmp_path / ".ddw-state.json").write_text(
        json.dumps(
            {
                "phase": "PLAN",
                "tier": "FEATURE",
                "ticket": "T-1",
                "title": "OpenSpec plan",
                "autonomy": "autonomous",
                "gates": {"define": True},
                "history": [
                    {
                        "timestamp": "2026-10-30T00:00:00Z",
                        "from": "IDLE",
                        "to": "CLASSIFY",
                        "action": "classify",
                        "tier": "FEATURE",
                        "ticket": "T-1",
                        "autonomy": "autonomous",
                    },
                    {
                        "timestamp": "2026-10-30T00:00:01Z",
                        "from": "CLASSIFY",
                        "to": "DEFINE",
                        "action": "feature",
                        "tier": "FEATURE",
                        "ticket": "T-1",
                        "autonomy": "autonomous",
                    },
                    {
                        "timestamp": "2026-10-30T00:00:02Z",
                        "from": "DEFINE",
                        "to": "PLAN",
                        "action": "validated PRD",
                        "tier": "FEATURE",
                        "ticket": "T-1",
                        "autonomy": "autonomous",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def call_patch_hook(
    project: Path, paths: list[str], *, update: bool = False
) -> subprocess.CompletedProcess[str]:
    patch = "*** Begin Patch\n" + "".join(
        (
            f"*** Update File: {path}\n@@\n-before\n+after\n"
            if update
            else f"*** Add File: {path}\n+# Planned document\n"
        )
        for path in paths
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


@pytest.mark.parametrize(
    "name", ["proposal.md", "design.md", "tasks.md", "specs/ledger/spec.md"]
)
def test_plan_allows_each_openspec_document(project: Path, name: str) -> None:
    result = call_patch_hook(project, [f"openspec/changes/add-ledger/{name}"])
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ALLOW"


@pytest.mark.parametrize("setting", [None, "## Other\nSpec format: OpenSpec\n"])
def test_plan_rejects_artifacts_without_exact_opt_in(
    project: Path, setting: str | None
) -> None:
    path = project / "AGENTS.md"
    if setting is None:
        path.unlink()
    else:
        path.write_text(setting, encoding="utf-8")
    result = call_patch_hook(project, ["openspec/changes/add-ledger/proposal.md"])
    assert result.returncode == 2
    assert "not one of its artifacts" in result.stdout


@pytest.mark.parametrize("phase", ["IDLE", "CLASSIFY", "DEFINE", "VERIFY", "DISCOVERY"])
def test_planning_artifact_is_not_allowed_in_other_phases(
    project: Path, phase: str
) -> None:
    state_path = project / ".ddw-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["phase"] = phase
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = call_patch_hook(project, ["openspec/changes/add-ledger/proposal.md"])
    assert result.returncode == 2
    assert f"the {phase} phase" in result.stdout


@pytest.mark.parametrize(
    "path",
    [
        "src/app.py",
        "openspec/specs/ledger/spec.md",
        "openspec/config.yaml",
        "openspec/changes/add-ledger/main.py",
        "openspec/changes/add-ledger/notes.md",
        "openspec/changes/add-ledger/specs/ledger/main.py",
        "openspec/changes/add-ledger/specs/ledger/nested/spec.md",
        "openspec/changes/archive/proposal.md",
        "openspec/changes/archive/2026-10-20-add-ledger/proposal.md",
        "openspec/changes/Add-Ledger/proposal.md",
        "openspec/changes/-add-ledger/proposal.md",
        "openspec/changes/add-ledger-/proposal.md",
    ],
)
def test_plan_still_rejects_source_and_non_artifact_paths(
    project: Path, path: str
) -> None:
    result = call_patch_hook(project, [path])
    assert result.returncode == 2
    assert "not one of its artifacts" in result.stdout


def test_mixed_patch_does_not_hide_source_behind_a_planning_document(
    project: Path,
) -> None:
    result = call_patch_hook(
        project,
        ["openspec/changes/add-ledger/proposal.md", "src/app.py"],
    )
    assert result.returncode == 2
    assert "`src/app.py`" in result.stdout


@pytest.mark.parametrize("directory_link", [False, True])
def test_planning_paths_cannot_alias_product_source(
    project: Path, directory_link: bool
) -> None:
    source = project / "src"
    source.mkdir()
    change = project / "openspec/changes/add-ledger"
    change.mkdir(parents=True)
    if directory_link:
        (change / "specs").symlink_to(source, target_is_directory=True)
        path = "openspec/changes/add-ledger/specs/ledger/spec.md"
    else:
        (change / "proposal.md").symlink_to(source / "app.py")
        path = "openspec/changes/add-ledger/proposal.md"
    result = call_patch_hook(project, [path])
    assert result.returncode == 2
    assert "`src/" in result.stdout


def test_hardlinked_planning_document_cannot_change_source(project: Path) -> None:
    source = project / "src/app.py"
    source.parent.mkdir()
    source.write_text("before\n", encoding="utf-8")
    artifact = project / "openspec/changes/add-ledger/proposal.md"
    artifact.parent.mkdir(parents=True)
    os.link(source, artifact)
    result = call_patch_hook(project, [str(artifact)], update=True)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "hard link" in result.stdout.lower()
    assert source.read_text(encoding="utf-8") == "before\n"


def test_uninspectable_planning_document_is_rejected(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    artifact = project / "openspec/changes/add-ledger/proposal.md"
    spec = importlib.util.spec_from_file_location(
        "openspec_plan_transition", ROOT / "ddw/scripts/validate-transition.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    original_stat = os.stat

    def denied_stat(path: os.PathLike[str] | str, *args: object, **kwargs: object):
        if str(path) == str(artifact):
            raise PermissionError("fixture: cannot inspect artifact")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", denied_stat)
    reason = module.source_write_denied(str(artifact), str(project), "PLAN")
    assert reason is not None
    assert "cannot inspect" in reason


def test_method_enforcement_remains_sealed(project: Path) -> None:
    result = call_patch_hook(project, [".ddw/scripts/validate-transition.py"])
    assert result.returncode == 2
    assert "DDW" in result.stdout


def test_validated_openspec_change_remains_sealed_in_code(project: Path) -> None:
    spec = project / "docs/ddw/specs/spec-T-1.md"
    spec.parent.mkdir(parents=True)
    spec.write_text(
        "| OpenSpec change | openspec/changes/add-ledger |\n", encoding="utf-8"
    )
    state_path = project / ".ddw-state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(phase="CODE", gates={"define": True, "spec": True, "threat": True})
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = call_patch_hook(project, ["openspec/changes/add-ledger/tasks.md"])
    assert result.returncode == 2
    assert "Return to PLAN" in result.stdout


@pytest.mark.parametrize(
    "extra",
    [
        None,
        "openspec/changes/add-ledger/app.py",
        "openspec/specs/ledger/spec.md",
        "openspec/config.yaml",
        "src/app.py",
    ],
)
def test_post_hook_distinguishes_untracked_planning_docs_from_source(
    project: Path, extra: str | None
) -> None:
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    prd = project / "docs/ddw/prd/prd-T-1.md"
    prd.parent.mkdir(parents=True)
    prd.write_text("# Validated PRD fixture\n", encoding="utf-8")
    ddw_receipt.write(str(prd), "prd", prd.read_text(encoding="utf-8"), "FEATURE")
    for name in ("proposal.md", "design.md", "tasks.md", "specs/ledger/spec.md"):
        path = project / "openspec/changes/add-ledger" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Planned artifact\n", encoding="utf-8")
    if extra is not None:
        path = project / extra
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("unapproved\n", encoding="utf-8")

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
  await hooks["execute.after"]({tool: "patch"});
} finally {
  dispose();
}
"""
    adapter = ROOT / "adapters/opencode/plugin/ddw.js"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", program, adapter.as_uri(), str(project)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    journal = project / ".ddw-journal.jsonl"
    entries = [
        json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()
    ]
    notices = [entry["note"] for entry in entries if entry.get("record") == "notice"]
    if extra is None:
        assert not notices, notices
    else:
        assert len(notices) == 1 and extra in notices[0], notices


def test_v2_shell_runs_the_canonical_atomic_transition(project: Path) -> None:
    state_path = project / ".ddw-state.json"
    state_path.write_text(
        json.dumps(
            {
                "phase": "IDLE",
                "tier": None,
                "ticket": None,
                "autonomy": None,
                "gates": {},
                "history": [],
            }
        ),
        encoding="utf-8",
    )
    program = """
import {execFileSync} from "node:child_process";
import {readFileSync} from "node:fs";
import {join} from "node:path";
const {default: plugin} = await import(process.argv[1]);
const project = process.argv[2];
const hooks = {};
const dispose = await plugin.setup({
  location: {directory: project},
  tool: {hook: async (name, callback) => { hooks[name] = callback; }},
  session: {hook: async () => {}},
  event: {subscribe: async function* () {}},
});
const command = '.ddw/scripts/transition.py --to CLASSIFY --tier FEATURE ' +
  '--ticket T-1 --title "Atomic transition" --action classify --write';
try {
  await hooks["execute.before"]({tool: "shell", input: {command}});
  execFileSync("python3", [
    join(project, ".ddw/scripts/transition.py"),
    "--state", join(project, ".ddw-state.json"),
    "--to", "CLASSIFY", "--tier", "FEATURE", "--ticket", "T-1",
    "--title", "Atomic transition", "--action", "classify", "--write",
  ], {cwd: project, stdio: ["ignore", "pipe", "pipe"]});
  await hooks["execute.after"]({tool: "shell", input: {command}});
  const state = JSON.parse(readFileSync(join(project, ".ddw-state.json"), "utf8"));
  if (state.phase !== "CLASSIFY" || state.history.length !== 1) process.exitCode = 3;
} finally {
  dispose();
}
"""
    adapter = ROOT / "adapters/opencode/plugin/ddw.js"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", program, adapter.as_uri(), str(project)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "CLASSIFY"
    assert state["history"][-1]["from"] == "IDLE"
    assert state["history"][-1]["to"] == "CLASSIFY"


def test_active_transition_commands_use_atomic_write() -> None:
    paths = [
        ROOT / "ddw/orchestrator.md",
        ROOT / "ddw/rules/classify.instructions.md",
        ROOT / "ddw/rules/define.instructions.md",
        ROOT / "ddw/rules/verify.instructions.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    forbidden = [
        "copy its\n   stdout into a `Write`",
        "Copy its stdout into a `Write`",
        "Paste its output in a\n   single write",
        "Paste the helper's output VERBATIM",
        "full-file `Write` (fallback)",
    ]
    assert not [phrase for phrase in forbidden if phrase in combined]

    command_blocks = re.findall(
        r"```bash\n(.*?\.ddw/scripts/transition\.py.*?)\n```",
        combined,
        flags=re.DOTALL,
    )
    assert command_blocks
    assert all("--write" in command for command in command_blocks), command_blocks
    assert 'pause: <ticket> — <reason>" --write' in combined
    assert "--clear-gate verify --write" in combined
    assert "sanctioned exception" in combined


def test_transition_preview_points_to_atomic_write(tmp_path: Path) -> None:
    state = tmp_path / ".ddw-state.json"
    state.write_text(
        json.dumps(
            {
                "phase": "IDLE",
                "tier": None,
                "ticket": None,
                "gates": {},
                "history": [],
            }
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            sys.executable,
            ROOT / "ddw/scripts/transition.py",
            "--state",
            state,
            "--graph",
            ROOT / "ddw/rules/transition-graph.json",
            "--to",
            "CLASSIFY",
            "--tier",
            "FEATURE",
            "--ticket",
            "T-1",
            "--title",
            "Test ticket",
            "--action",
            "classify",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PREVIEW" in result.stderr
    assert "rerun this command with `--write`" in result.stderr
    assert "copy the JSON" not in result.stderr
