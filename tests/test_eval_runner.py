"""The eval harness, asked directly.

`evals/runner.py` is the instrument the instruction layer is measured with,
and an instrument nobody measures reports whatever. The scenarios exercise it
end to end — each one stands up a repo, installs the method and runs the
verdict — so they cost minutes: here go the capabilities of the harness
itself, which can be asked in milliseconds and are precisely the ones that,
broken silently, make a scenario pass for the wrong reason.

The three tested here arrived together and for the same reason: without them,
a whole family of scenarios could be written but not judged.

  · `given.files` — seeding the tree. Before, only `.ddw-state.json` could be
    written, so every scenario about a source file that already exists started
    from the fixture's empty repo — that is, measuring something else.
  · `expect.file_matches` — asserting on the CONTENT. `files_present` cannot
    tell the document that answers the question from the one that makes it
    up: both exist.
  · `kind: method-lint` — `lint_method.py` as a verdict, with its control.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
import tomllib

import pytest

pytest.importorskip("yaml")  # the runner exits with exit(2) without it; do not hang collection

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, rel))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


runner = _load("ddw_eval_runner", "evals/runner.py")


@pytest.fixture()
def repo(tmp_path):
    """A pretend git repo, shaped the way `make_repo` leaves one."""
    r = tmp_path / "repo"
    r.mkdir()
    for cmd in (["git", "init", "-q"],
                ["git", "config", "user.email", "eval@example.com"],
                ["git", "config", "user.name", "eval"],
                ["git", "config", "commit.gpgsign", "false"]):
        subprocess.run(cmd, cwd=r, check=True, capture_output=True)
    (r / "README.md").write_text("# fixture\n")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=r, check=True, capture_output=True)
    return r


# ── given.files: seeding the tree ────────────────────────────────────────────

def test_seed_files_writes_and_commits(repo):
    n = runner.seed_files(repo, {"src/account.py": "def delete(): ...\n",
                                 "docs/ddw/prd/prd-T-1.md": "# PRD\n"})
    assert n == 2
    assert (repo / "src" / "account.py").read_text() == "def delete(): ...\n"
    tracked = subprocess.run(["git", "ls-files"], cwd=repo,
                             capture_output=True, text=True).stdout.split()
    assert "src/account.py" in tracked and "docs/ddw/prd/prd-T-1.md" in tracked
    # And the tree ends up clean: a seeded, uncommitted source file is
    # uncommitted work the scenario did not ask for, and rules get measured on that.
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=repo,
                           capture_output=True, text=True).stdout.strip()
    assert dirty == ""


def test_seed_files_can_leave_them_uncommitted(repo):
    runner.seed_files(repo, {"src/a.py": "x\n"}, commit=False)
    # `-uall`: with the default, git collapses the new directory into `?? src/`
    # and the assertion on the full path says nothing.
    dirty = subprocess.run(["git", "status", "--porcelain", "-uall"], cwd=repo,
                           capture_output=True, text=True).stdout
    assert "?? src/a.py" in dirty


def test_seed_files_commits_what_the_install_ignores(repo):
    # The install leaves a block in `.gitignore`, and what it ignores — the
    # paused tickets, the session markers, the journal — is part of what a
    # scenario needs to seed. Without `-f` the commit finds nothing, and the
    # fixture silently ends up different from what the scenario says.
    (repo / ".gitignore").write_text(".ddw-paused/\n")
    runner.seed_files(repo, {".ddw-paused/T-2.json": '{"ticket": "T-2"}\n'})
    tracked = subprocess.run(["git", "ls-files"], cwd=repo,
                             capture_output=True, text=True).stdout.split()
    assert ".ddw-paused/T-2.json" in tracked


def test_seed_files_does_not_write_outside_the_fixture(repo):
    # The control that rewrote the checkout of whoever ran it cost twenty
    # minutes of believing the product had a bug. Nobody comes in this way again.
    with pytest.raises(RuntimeError, match="outside the fixture"):
        runner.seed_files(repo, {"../escaped.py": "x"})
    with pytest.raises(RuntimeError, match="absolute path"):
        runner.seed_files(repo, {"/etc/passwd": "x"})
    assert not (repo.parent / "escaped.py").exists()


def test_seed_files_refuses_the_state_file(repo):
    with pytest.raises(RuntimeError, match="given.state"):
        runner.seed_files(repo, {".ddw-state.json": "{}"})


# ── expect.file_matches: the content, not the existence ──────────────────────

def _judge(repo, expect):
    return runner.judge_repo_state({"expect": expect}, repo, [])


def test_file_matches_reads_the_artifact(repo):
    (repo / "prd.md").write_text("NFR: el endpoint responde en menos de 5 segundos (p95)\n")
    v, _ = _judge(repo, {"file_matches": [{"path": "prd.md", "matches": r"p95"}]})
    assert v == runner.PASS

    v, d = _judge(repo, {"file_matches": [
        {"path": "prd.md", "absent": r"\d+\s*segundos"}]})
    assert v == runner.FAIL and "must not" in d


def test_file_matches_accepts_a_list_and_reports_each_miss(repo):
    (repo / "prd.md").write_text("# PRD\n")
    v, d = _judge(repo, {"file_matches": [
        {"path": "prd.md", "matches": ["NFR", "criterio de aceptación"]}]})
    assert v == runner.FAIL
    assert d.count("does not contain") == 2


def test_file_matches_is_case_sensitive_unless_the_scenario_says_otherwise(repo):
    (repo / "prd.md").write_text("Menos de 5 Segundos\n")
    v, _ = _judge(repo, {"file_matches": [{"path": "prd.md", "absent": r"\d+ segundos"}]})
    assert v == runner.PASS
    v, _ = _judge(repo, {"file_matches": [{"path": "prd.md", "absent": r"(?i)\d+ segundos"}]})
    assert v == runner.FAIL


def test_file_matches_on_a_missing_file_is_a_problem_not_a_pass(repo):
    v, d = _judge(repo, {"file_matches": [{"path": "gone.md", "absent": "cualquier cosa"}]})
    assert v == runner.FAIL and "does not exist" in d


def test_a_clause_that_asserts_nothing_raises(repo):
    # An assertion that cannot judge is the same as not writing it, and here
    # that reads as green. It raises: `run_one` turns it into ERROR, which is red.
    (repo / "prd.md").write_text("x")
    with pytest.raises(RuntimeError, match="asserts nothing"):
        _judge(repo, {"file_matches": [{"path": "prd.md"}]})


def test_a_regex_that_does_not_compile_raises(repo):
    (repo / "prd.md").write_text("x")
    with pytest.raises(RuntimeError, match="does not compile"):
        _judge(repo, {"file_matches": [{"path": "prd.md", "matches": "("}]})


def test_file_matches_works_inside_any_of(repo):
    # `any_of` judges each branch with this same function; a capability that
    # does not reach inside it leaves half a family of scenarios unwritable.
    (repo / "prd.md").write_text("pendiente de plataforma\n")
    v, _ = _judge(repo, {"any_of": [[
        {"file_matches": [{"path": "prd.md", "matches": "pendiente"}]},
        {"files_absent": ["prd.md"]},
    ]]})
    assert v == runner.PASS


# ── kind: method-lint ────────────────────────────────────────────────────────

def _fake_method(tmp_path, body):
    root = tmp_path / "method"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "lint_method.py").write_text(body)
    return root


def test_method_lint_that_never_judged_is_error_not_red(tmp_path, repo):
    # A linter that falls over before printing its verdict came out neither
    # green nor red. Counting it as red gives away the whole control mode.
    root = _fake_method(tmp_path, "import sys\nsys.stderr.write('Traceback…\\n')\nsys.exit(1)\n")
    v, d = runner.run_method_lint({}, root, repo)
    assert v == runner.ERROR and "nothing was judged" in d


def test_method_lint_control_off_target(tmp_path, repo):
    root = _fake_method(tmp_path, "print('lint_method: 1 claim(s)')\nprint('  otra cosa')\n"
                                  "raise SystemExit(1)\n")
    sc = {"control": {"finding_matches": "el hueco que este escenario rompió"}}
    v, d = runner.run_method_lint(sc, root, repo, control=True)
    assert v == runner.ERROR and d.startswith("control off-target")


def test_method_lint_control_that_stays_green_is_a_failed_control(tmp_path, repo):
    root = _fake_method(tmp_path, "print('lint_method: every claim checks out')\n")
    sc = {"control": {"finding_matches": "lo que sea"}}
    v, d = runner.run_method_lint(sc, root, repo, control=True)
    assert v == runner.FAIL and "stayed green" in d


def test_method_lint_green_tree_passes(tmp_path, repo):
    root = _fake_method(tmp_path, "print('lint_method: every claim checks out')\n")
    v, _ = runner.run_method_lint({}, root, repo)
    assert v == runner.PASS


# ── a control that cannot discriminate, said and counted separately ──────────

def test_a_control_that_cannot_discriminate_is_skipped_with_its_reason():
    # Measured: the `forged` control PASSES with a capable model, because its
    # three edits are prose and the hook rejects the write anyway. Leaving it
    # red forever trains everyone to ignore red; deleting it leaves the
    # scenario claiming to measure something it does not.
    import types
    sc = {"id": "x", "kind": "behavioral",
          "control": {"cannot_discriminate": "the rule is defended twice"}}
    args = types.SimpleNamespace(keep=False, agent="claude", model=None)
    r = runner.run_one(sc, os.path.join(ROOT), args, control=True)
    assert r.verdict == runner.SKIP
    assert "defended twice" in r.detail


def test_the_excuse_does_nothing_in_a_normal_run():
    # Only control mode looks at it: in normal mode the scenario has to run
    # like any other, or an excuse about the control would switch off the whole measurement.
    import inspect
    src = inspect.getsource(runner.run_one)
    head = src[:src.index("workdir = Path(")]
    assert "if control:" in head and "cannot_discriminate" in head


# ── control mode does not accept a red that belongs to the harness ───────────

@pytest.mark.parametrize("detail", [
    "KeyError: 'when'",
    "TimeoutExpired: …",
    "control unavailable: the anchor is gone",
    "control off-target: lint_method went red on another claim",
    # Measured in the cloud: a control got counted as red because the CLI
    # exited 1 without ever answering. The regression was never put in front of anyone.
    "the agent did not complete a turn (exit 1): ",
    "the agent produced no JSON events — the turn did not run",
])
def test_a_red_that_is_the_harness_does_not_pass_the_control(detail):
    import inspect
    src = inspect.getsource(runner.main)
    block = src[src.index("if args.control:"):src.index("results.append(r)")]
    # All four families have to be named in the branch that turns a harness
    # ERROR into a FAILED control, not into a legitimate red.
    needles = ("_harness", "_no_turn", "control unavailable", "control off-target",
               "the agent did not complete a turn", "the agent produced no JSON events")
    assert all(n in block or n in src for n in needles)
    assert "the control proved nothing" in block


# ── a control tied to a branch has an expiry date ────────────────────────────

def test_every_restored_commit_is_reachable_from_the_main_line():
    """A `restore_from_commit` pointing at a branch's commit dies with the branch.

    Measured: the squash-merge of PR #7 took the history of `feat/docs-audit`
    with it, and the control of `painted-door-install-doc-eject` — which
    restored `docs/INSTALL.md` at `4c41f3e^` — started failing from `main` for
    not being applicable. A control that cannot be applied proves nothing, and
    the scenario goes red for a reason that is not its own.
    """
    import glob
    import yaml
    # This question is about the REPOSITORY, not the tree: without `.git`
    # there is no main line to measure against, and `git merge-base` fails for
    # not being able to run — which is indistinguishable from an inapplicable
    # control.
    #
    # The `scripts/mutate.py` sandbox copies the tree with
    # `ignore_patterns(".git", …)`, so here this failed on EVERY unmutated
    # copy. The runner's red-baseline guard caught it and refused to inject —
    # meaning the mutation run could not start, locally or in CI, and the
    # cause was this test asking something that has no answer on a copy.
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        pytest.skip("no git history: the question cannot be asked of a copy of the tree")
    offenders = []
    for path in sorted(glob.glob(os.path.join(ROOT, "evals/scenarios/*.yaml"))):
        sc = yaml.safe_load(open(path, encoding="utf-8"))
        ctl = sc.get("control") or {}
        if ctl.get("type") != "restore_from_commit":
            continue
        commit = ctl["commit"]
        # Is it on the main line of THIS checkout? `HEAD` is enough because
        # every working branch comes off `main`; what gets ruled out is the
        # commit that lives only on another branch.
        r = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                           cwd=ROOT, capture_output=True)
        if r.returncode != 0:
            offenders.append(f"{os.path.basename(path)} restores at {commit}")
    assert not offenders, (
        "these controls hang off history the main line does not carry, so they stop being "
        "applicable the day that branch is deleted — use `type: substitute`: " + "; ".join(offenders))


def test_a_scenario_with_work_in_progress_says_which_branch_it_is_on():
    """A state that says work in progress, on `master`, is an impossible world.

    The boot orders a branch check before resuming: on a generic one —
    `main`, `master`, `develop`… — the agent STOPS and asks about the
    inconsistency. That is the right behavior, and if the harness put it
    there the scenario spends a turn on something it does not measure.
    Measured with Claude Code: the entire first turn went to that.
    """
    import glob
    import yaml
    GENERIC = {None, "IDLE"}
    offenders = []
    for path in sorted(glob.glob(os.path.join(ROOT, "evals/scenarios/*.yaml"))):
        sc = yaml.safe_load(open(path, encoding="utf-8"))
        if sc.get("kind") != "behavioral":
            continue
        state = (sc.get("given") or {}).get("state")
        if not isinstance(state, dict) or state.get("phase") in GENERIC:
            continue
        if not (sc.get("given") or {}).get("branch"):
            offenders.append(f"{os.path.basename(path)} is in {state.get('phase')}")
    assert not offenders, (
        "these scenarios start with work in progress on a generic branch, so the boot's own "
        "consistency check fires and the turn goes to a contradiction the harness invented: "
        + "; ".join(offenders))


# ── the real scenario, read as data ──────────────────────────────────────────

def test_the_method_lint_scenario_declares_a_control_with_a_target(tmp_path):
    import yaml
    p = os.path.join(ROOT, "evals/scenarios/method-lint-still-knows-how-to-go-red.yaml")
    sc = yaml.safe_load(open(p, encoding="utf-8"))
    assert sc["kind"] == "method-lint"
    # Without `finding_matches` the control settles for any red, which is
    # exactly what this kind exists to not accept.
    assert sc["control"]["finding_matches"]
    assert sc["kind"] in runner.OFFLINE_KINDS, "CI runs `--offline`; outside it this never runs"
    # And the control's anchor has to still exist in the tree, or the control
    # cannot be applied and proves nothing.
    for edit in sc["control"]["edits"]:
        text = open(os.path.join(ROOT, edit["path"]), encoding="utf-8").read()
        assert edit["old"] in text, f"the control's anchor is no longer in {edit['path']}"


def test_json_import_is_used_by_seed_files(repo):
    # A `given.files` whose value is not text gets written as JSON: it is how
    # a scenario seeds a manifest or another file's state without escaping it
    # by hand in the YAML.
    runner.seed_files(repo, {"pkg.json": {"name": "x"}})
    assert json.loads((repo / "pkg.json").read_text())["name"] == "x"


# ── Copilot: the level of the wiring is what decides whether anything gates ──
#
# Copilot was never measured end to end because it had no headless profile.
# These cases pin what the profile has to get right, and each one corresponds
# to a way of coming out green without having measured anything.


def test_copilot_has_a_headless_profile():
    """Without an entry, `agent_profile` raises and the scenario does not run.
    With a wrong one, it runs and measures something else: which is why the
    flags are asserted too."""
    name, prof = runner.agent_profile(["copilot"])
    assert name == "copilot"
    assert "--allow-all" in prof["flags"], "without --allow-all, -p mode does not even start"
    assert prof["prompt"] == ["-p", "{turn}"], "-p is the headless entry point"
    assert prof["dir"] == ["-C", "{repo}"], "without -C the run can point at another tree"


def test_copilot_turn_refuses_to_run_without_its_own_home():
    """HOME is where Copilot's gates live. Pointed at the operator's, the
    fixture gets judged by that machine's wiring and the scenario reports as
    product what belongs to the harness."""
    with pytest.raises(RuntimeError) as e:
        runner.agent_turn_cmd(["copilot"], "gpt-5-mini", None, "hacé algo", repo="/tmp/x")
    assert "HOME" in str(e.value)


def test_copilot_turn_sets_home_and_repo(tmp_path):
    name, cmd, env = runner.agent_turn_cmd(["copilot"], "gpt-5-mini", None, "hacé algo",
                                           repo=tmp_path / "repo", home=tmp_path / "home")
    assert env["HOME"] == str(tmp_path / "home")
    assert "-C" in cmd and str(tmp_path / "repo") in cmd
    assert cmd[-2:] == ["-p", "hacé algo"]


def test_copilot_resumes_the_session_it_was_given(tmp_path):
    _, cmd, _ = runner.agent_turn_cmd(["copilot"], "gpt-5-mini", "abc-123", "seguí",
                                      repo=tmp_path / "repo", home=tmp_path / "home")
    assert "--resume=abc-123" in cmd


def test_copilot_reads_the_session_id_out_of_the_resume_footer():
    out = "hecho\n\nResume     copilot --resume=48ac578f-15de-4b10-b255-c2d4518af86c\n"
    payload, session, err = runner.agent_turn_read("copilot", out)
    assert err is None
    assert session == "48ac578f-15de-4b10-b255-c2d4518af86c"
    assert payload == out


def test_copilot_without_a_resume_id_is_an_error_not_a_pass():
    """A turn without an id is not a detail: the next turn opens another
    session, the model does not see what it just did, and the scenario
    measures first turns while claiming it measured a conversation."""
    payload, session, err = runner.agent_turn_read("copilot", "hecho, saludos")
    assert session is None
    assert err and "new session" in err


def test_the_copilot_tap_wraps_the_repo_hooks(tmp_path):
    """The user-level wiring runs THE REPO'S script by relative path, so
    wrapping the repo's copy is the only thing there is to wrap."""
    hooks = tmp_path / ".github" / "hooks" / "ddw"
    hooks.mkdir(parents=True)
    for name in ("pre-tool-use.sh", "post-write.sh", "pre-compact.sh"):
        (hooks / name).write_text("#!/usr/bin/env bash\nexit 0\n")
    assert runner._tap_copilot(tmp_path) == 2
    for name in ("pre-tool-use.sh", "post-write.sh"):
        assert (hooks / (name[:-3] + ".real.sh")).exists()
        assert "DDW_EVAL_PAYLOAD" in (hooks / name).read_text()
    # pre-compact is not a verdict: wrapping it would put an "allowed" in the
    # log for a write that never existed.
    assert not (hooks / "pre-compact.real.sh").exists()


def test_the_copilot_home_refuses_to_run_unauthenticated(tmp_path, monkeypatch):
    """A blank HOME has no credentials, and Copilot fails in a way that reads
    as a verdict about the product."""
    fake = tmp_path / "fakehome"
    (fake / ".copilot").mkdir(parents=True)
    (fake / ".copilot" / "config.json").write_text("{}")
    monkeypatch.setenv("HOME", str(fake))
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: p.replace("~", str(fake)) if p.startswith("~") else p)
    with pytest.raises(RuntimeError) as e:
        runner.copilot_home(tmp_path / "wd", tmp_path / "wd" / "repo")
    assert "credentials" in str(e.value)


def test_the_copilot_home_carries_credentials_and_trusts_the_fixture(tmp_path, monkeypatch):
    fake = tmp_path / "fakehome"
    (fake / ".copilot").mkdir(parents=True)
    # JSONC: the real file opens with two `//` lines. Parsed as strict JSON it
    # blows up, the credentials get lost in silence, and the run fails at
    # authentication.
    (fake / ".copilot" / "config.json").write_text(
        '// managed automatically\n{"copilotTokens": {"t": 1}, "trustedFolders": ["/otro"]}\n')
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: p.replace("~", str(fake)) if p.startswith("~") else p)
    wd = tmp_path / "wd"
    repo = wd / "repo"
    home = runner.copilot_home(wd, repo)
    cfg = json.loads((home / ".copilot" / "config.json").read_text())
    assert cfg["copilotTokens"] == {"t": 1}
    assert str(repo) in cfg["trustedFolders"] and "/otro" in cfg["trustedFolders"]
    # And NOTHING else crosses over: the session, the settings and the hooks start empty.
    assert sorted(p.name for p in (home / ".copilot").iterdir()) == ["config.json"]


# ── What the harness has to get right for a run to mean anything ─────────────
#
# The three below came out of mutations that SURVIVED: the code was right and
# nothing was holding it up. A fault nobody kills is a fault that comes back.


def _fake_install(runner_mod, monkeypatch, calls, wire_hooks=True):
    """Replaces `sh` with something that fakes the install and records how it was called."""
    real_sh = runner_mod.sh

    def fake(cmd, cwd=None, env=None, timeout=120, stdin=None):
        calls.append({"cmd": cmd, "env": env})
        if any("install.sh" in str(c) for c in cmd):
            repo = pathlib.Path(cmd[2])
            (repo / ".ddw" / "scripts").mkdir(parents=True, exist_ok=True)
            (repo / ".ddw" / "scripts" / "hook-gate.py").write_text("x")
            if wire_hooks and env and env.get("HOME"):
                d = pathlib.Path(env["HOME"]) / ".copilot" / "hooks"
                d.mkdir(parents=True, exist_ok=True)
                (d / "ddw.json").write_text("{}")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_sh(cmd, cwd=cwd, env=env, timeout=timeout, stdin=stdin)

    monkeypatch.setattr(runner_mod, "sh", fake)


def _fake_creds(runner_mod, monkeypatch, tmp_path):
    fake = tmp_path / "fakehome"
    (fake / ".copilot").mkdir(parents=True, exist_ok=True)
    (fake / ".copilot" / "config.json").write_text('{"copilotTokens": {"t": 1}}')
    monkeypatch.setattr(os.path, "expanduser",
                        lambda p: p.replace("~", str(fake)) if p.startswith("~") else p)


def test_a_copilot_install_never_touches_the_operators_own_home(tmp_path, monkeypatch):
    """Installing for Copilot writes OUTSIDE the fixture, in `$HOME`. Without
    a HOME of its own, a single eval run rewires every Copilot session on the
    machine, and two parallel runs rewire each other."""
    _fake_creds(runner, monkeypatch, tmp_path)
    calls = []
    _fake_install(runner, monkeypatch, calls)
    wd = tmp_path / "wd"
    runner.make_repo(tmp_path / "src", "copilot", wd)
    install = [c for c in calls if any("install.sh" in str(x) for x in c["cmd"])][0]
    assert install["env"] is not None, "the Copilot install ran with the operator's HOME"
    assert install["env"]["HOME"] == str(wd / "home")
    assert install["env"]["HOME"] != os.environ.get("HOME")


def test_a_copilot_install_that_wired_no_hooks_is_refused(tmp_path, monkeypatch):
    """Exiting 0 without wiring anything is the exact way this looked healthy:
    the scenario runs, nothing judges, and the result reads as a clean pass."""
    _fake_creds(runner, monkeypatch, tmp_path)
    calls = []
    _fake_install(runner, monkeypatch, calls, wire_hooks=False)
    with pytest.raises(RuntimeError) as e:
        runner.make_repo(tmp_path / "src", "copilot", tmp_path / "wd")
    assert "user-level" in str(e.value)


def test_the_verdict_tap_covers_copilot(tmp_path, monkeypatch):
    """`_tap_copilot` can be perfect and called by nobody."""
    monkeypatch.setattr(runner, "_tap_claude", lambda repo: 0)
    monkeypatch.setattr(runner, "_tap_opencode", lambda repo: 0)
    hooks = tmp_path / ".github" / "hooks" / "ddw"
    hooks.mkdir(parents=True)
    for name in ("pre-tool-use.sh", "post-write.sh"):
        (hooks / name).write_text("#!/usr/bin/env bash\nexit 0\n")
    assert runner.install_verdict_tap(tmp_path) == 2


# ── Codex: explicit fixture trust, resumable JSONL, and real hook verdicts ────

def _codex_events() -> list[dict]:
    return [
        {"type": "thread.started", "thread_id": "thread-123"},
        {"type": "turn.started"},
        {"type": "item.completed", "item": {
            "id": "item_1", "type": "agent_message", "text": "The write is refused."}},
        {"type": "turn.completed", "usage": {
            "input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5,
            "reasoning_output_tokens": 0}},
    ]


def _jsonl(events: list[dict]) -> str:
    return "\n".join(json.dumps(event) for event in events) + "\n"


@pytest.mark.parametrize("session", [None, "thread-123"])
@pytest.mark.parametrize("entrypoint", [["codex"], ["codex", "exec"]])
def test_codex_commands_keep_the_sandbox_and_resume_signature(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    session: str | None, entrypoint: list[str],
) -> None:
    """Resume has no --cd or --sandbox; config overrides govern both turns."""
    root = (tmp_path / 'fixture "with spaces"').resolve()
    codex_home = tmp_path / "isolated-codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    name, cmd, env = runner.agent_turn_cmd(
        entrypoint, "test-model", session, "--literal prompt", repo=root)
    global_flags = [
        "-c", 'approval_policy="never"',
        "-c", 'sandbox_mode="workspace-write"',
        "-c", f'projects.{json.dumps(str(root))}.trust_level="trusted"',
    ]
    flags = ["--json", "--ignore-user-config", "--dangerously-bypass-hook-trust",
             "--model", "test-model"]
    tail = (["resume", *flags, "--", session, "--literal prompt"] if session else
            ["--sandbox", "workspace-write", "--cd", str(root), *flags,
             "--", "--literal prompt"])
    assert name == "codex"
    assert cmd == ["codex", *global_flags, "exec", *tail]
    trust = tomllib.loads(global_flags[-1])
    assert trust["projects"][str(root)]["trust_level"] == "trusted"
    assert env["PWD"] == str(root)
    assert env["CODEX_HOME"] == str(codex_home.resolve())
    assert not codex_home.exists(), "the harness must not create or copy auth/config files"


def test_codex_requires_a_fixture_root() -> None:
    """A missing root must not accidentally trust or run in the operator's tree."""
    with pytest.raises(RuntimeError, match="fixture"):
        runner.agent_turn_cmd(["codex"], None, None, "Do something")


def test_codex_requires_an_explicit_non_default_home(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hook-trust bypass is safe only when caller-owned user state is isolated."""
    operator_home = tmp_path / "operator-home"
    default_home = operator_home / ".codex"
    default_home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(operator_home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    with pytest.raises(RuntimeError, match="CODEX_HOME must be explicitly"):
        runner.agent_turn_cmd(["codex"], None, None, "Do something", repo=tmp_path / "fixture")

    monkeypatch.setenv("CODEX_HOME", str(default_home))
    with pytest.raises(RuntimeError, match="must not be the operator's default"):
        runner.agent_turn_cmd(["codex"], None, None, "Do something", repo=tmp_path / "fixture")

    alias = tmp_path / "codex-alias"
    alias.symlink_to(default_home, target_is_directory=True)
    monkeypatch.setenv("CODEX_HOME", str(alias))
    with pytest.raises(RuntimeError, match="must not be the operator's default"):
        runner.agent_turn_cmd(["codex"], None, None, "Do something", repo=tmp_path / "fixture")


@pytest.mark.parametrize("arguments", [
    ["--dangerously-bypass-approvals-and-sandbox"], ["--sandbox", "danger-full-access"],
    ["exec", "resume", "--last"], ["--disable", "hooks"],
])
def test_codex_caller_flags_cannot_disable_fixture_protections(arguments: list[str]) -> None:
    """Only the harness constructs sandbox, output, hook, and session options."""
    with pytest.raises(RuntimeError, match="harness controls"):
        runner.agent_turn_cmd(["codex", *arguments], None, None, "Do something", repo="/fixture")


def test_codex_reads_a_completed_thread_without_using_model_prose() -> None:
    """The event objects remain available for transcript-format assertions."""
    events = _codex_events()
    payload, session, error = runner.agent_turn_read("codex", _jsonl(events))
    assert error is None
    assert session == "thread-123"
    assert payload == events


@pytest.mark.parametrize("stdout", [
    "", "The write is refused.", "[]\n", "null\n", "42\n", "{}\n",
    '{"type": "thread.started",\n',
    _jsonl(_codex_events()) + "not JSON\n",
    _jsonl(_codex_events()) + '{"type": "item.completed",\n',
    '{"type": "thread.started", "thread_id": "one", "thread_id": "two"}\n'
    + _jsonl(_codex_events()[1:]),
    _jsonl(_codex_events()).replace('"input_tokens": 10', '"input_tokens": NaN'),
    _jsonl(_codex_events()).replace('"output_tokens": 5', '"output_tokens": Infinity'),
    _jsonl(_codex_events()[1:]),
    _jsonl(_codex_events()[:-1]),
    _jsonl([_codex_events()[0], _codex_events()[-1]]),
    _jsonl([*_codex_events()[:2], _codex_events()[-1]]),
    _jsonl([{"type": "thread.started", "thread_id": 123}, *_codex_events()[1:]]),
    _jsonl([{"type": "thread.started", "thread_id": ""}, *_codex_events()[1:]]),
    _jsonl([{"type": "thread.started", "thread_id": "  "}, *_codex_events()[1:]]),
    _jsonl([_codex_events()[0], _codex_events()[0], *_codex_events()[1:]]),
    _jsonl([_codex_events()[0], {"type": "thread.started", "thread_id": "other"},
            *_codex_events()[1:]]),
    _jsonl([*_codex_events(), _codex_events()[-1]]),
    _jsonl([*_codex_events()[:2], {"type": "unknown"}, *_codex_events()[2:]]),
    _jsonl([*_codex_events()[:2], {"type": "item.completed", "item": []},
            _codex_events()[-1]]),
    _jsonl([*_codex_events()[:2], {"type": "item.completed", "item": {
        "id": 3, "type": "agent_message", "text": "Done"}}, _codex_events()[-1]]),
    _jsonl([*_codex_events()[:2], {"type": "item.completed", "item": {
        "id": "item_1", "type": "agent_message", "text": 3}}, _codex_events()[-1]]),
    _jsonl([*_codex_events()[:2], {"type": "item.completed", "item": {
        "id": "item_1", "type": "agent_message", "text": ""}}, _codex_events()[-1]]),
    _jsonl([*_codex_events()[:-1], {"type": "turn.completed"}]),
    _jsonl([*_codex_events()[:-1], {"type": "turn.completed", "usage": []}]),
    _jsonl([*_codex_events()[:-1], {"type": "turn.completed", "usage": {
        "input_tokens": True, "cached_input_tokens": 0, "output_tokens": 5}}]),
    _jsonl([*_codex_events()[:-1], {"type": "turn.completed", "usage": {
        "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": -1}}]),
    _jsonl([*_codex_events()[:2], {"type": "item.started", "item": {
        "id": "unfinished", "type": "agent_message", "text": "Thinking"}},
        *_codex_events()[2:]]),
])
def test_codex_unjudgeable_streams_fail_closed(stdout: str) -> None:
    """A zero CLI exit cannot make malformed, ambiguous, or empty turns green."""
    _, _, error = runner.agent_turn_read("codex", stdout)
    assert error and error.startswith("the agent returned output this runner cannot parse")


@pytest.mark.parametrize("event", [
    {"type": "error", "message": "Connection failed"},
    {"type": "turn.failed", "error": {"message": "Model unavailable"}},
])
def test_codex_errors_are_not_completed_turns(event: dict) -> None:
    """Even an error following a completion invalidates the measurement."""
    _, _, error = runner.agent_turn_read("codex", _jsonl([*_codex_events(), event]))
    assert error and error.startswith("the agent reported an error")


def test_codex_parses_tool_lifecycles_including_a_refused_patch() -> None:
    """A failed tool is not a failed turn: the hook log determines enforcement."""
    command = {"id": "command", "type": "command_execution", "command": "ls",
               "aggregated_output": "", "exit_code": None, "status": "in_progress"}
    plan = {"id": "plan", "type": "todo_list", "items": [{"text": "Check", "completed": False}]}
    events = [*_codex_events()[:2],
              {"type": "item.started", "item": command},
              {"type": "item.completed", "item": dict(command, status="completed", exit_code=0)},
              {"type": "item.started", "item": plan},
              {"type": "item.updated", "item": plan},
              {"type": "item.completed", "item": plan},
              {"type": "item.completed", "item": {
                  "id": "patch", "type": "file_change", "status": "failed",
                  "changes": [{"path": "src/a.py", "kind": "add"}]}},
              *_codex_events()[2:]]
    payload, session, error = runner.agent_turn_read("codex", _jsonl(events))
    assert error is None and session == "thread-123" and payload == events


@pytest.mark.parametrize("item", [
    {"id": "bad", "type": "future_item"},
    {"id": "bad", "type": "command_execution", "command": "ls", "status": "completed"},
    {"id": "bad", "type": "file_change", "changes": "src/a.py", "status": "completed"},
    {"id": "bad", "type": "file_change", "changes": [{}], "status": "completed"},
    {"id": "bad", "type": "todo_list", "items": [{"text": "Check", "completed": "yes"}]},
    {"id": "bad", "type": "web_search", "query": "query", "action": []},
])
def test_codex_schema_invalid_tool_items_are_harness_errors(item: dict) -> None:
    events = [*_codex_events()[:2], {"type": "item.completed", "item": item}, *_codex_events()[2:]]
    assert runner.agent_turn_read("codex", _jsonl(events))[2] is not None


@pytest.fixture()
def codex_hooks(repo: pathlib.Path) -> pathlib.Path:
    """Fake adapter hooks capture bytes and produce independent hook outcomes."""
    hooks = repo / ".codex" / "hooks" / "ddw"
    hooks.mkdir(parents=True)
    for name, code in (("pre-tool-use.sh", 2), ("post-write.sh", 0)):
        (hooks / name).write_text(
            '#!/usr/bin/env bash\ncat > "$(dirname "$0")/captured-' + name + '"\n'
            'printf \'original stdout\\n\'\nprintf \'original stderr\\n\' >&2\n'
            f'exit {code}\n')
    for name in ("session-start.sh", "pre-compact.sh"):
        (hooks / name).write_text("#!/usr/bin/env bash\nexit 0\n")
    manifest = {f"codex:.codex/hooks/ddw/{name}": runner._fingerprint(hooks / name)
                for name in ("pre-tool-use.sh", "post-write.sh", "session-start.sh", "pre-compact.sh")}
    manifest["codex:.codex/hooks.json"] = "unchanged-wiring"
    (repo / ".ddw-installed.json").write_text(json.dumps(manifest))
    return hooks


def test_codex_taps_preserve_streams_exit_and_every_patch_path(
    repo: pathlib.Path, codex_hooks: pathlib.Path, tmp_path: pathlib.Path,
) -> None:
    """The fixture log, not CLAUDE_PROJECT_DIR or a payload cwd, owns the tap."""
    assert runner.install_verdict_tap(repo) == 2
    command = ("*** Begin Patch\n*** Add File: src/new.py\n+x\n"
               "*** Update File: src/old.py\n*** Move to: src/renamed.py\n"
               "@@\n-x\n+y\n*** Delete File: src/deleted.py\n*** End Patch\n")
    payload = (json.dumps({"cwd": str(tmp_path), "tool_name": "apply_patch",
                           "tool_input": {"command": command}}) + "\n\n").encode()
    nested = repo / "nested"
    nested.mkdir()
    for name, code in (("pre-tool-use.sh", 2), ("post-write.sh", 0)):
        result = subprocess.run(
            ["bash", str(codex_hooks / name)], input=payload, cwd=nested,
            env=dict(os.environ, CLAUDE_PROJECT_DIR=str(tmp_path)), capture_output=True)
        assert result.returncode == code
        assert result.stdout == b"original stdout\n"
        assert result.stderr == b"original stderr\n"
        assert (codex_hooks / f"captured-{name}").read_bytes() == payload
    rows = [json.loads(line) for line in (repo / ".ddw-eval-verdicts.jsonl").read_text().splitlines()]
    paths = ["src/new.py", "src/old.py", "src/renamed.py", "src/deleted.py"]
    assert [(row["path"], row["exit"]) for row in rows] == [
        (path, code) for code in (2, 0) for path in paths]
    assert all(row["tool"] == "apply_patch" for row in rows)
    assert not (tmp_path / ".ddw-eval-verdicts.jsonl").exists()
    assert _judge(repo, {"hook_refused": paths, "hook_ran": True})[0] == runner.PASS
    assert not (codex_hooks / "session-start.real.sh").exists()
    assert not (codex_hooks / "pre-compact.real.sh").exists()


def test_codex_tap_reseals_only_the_installed_hook_files(
    repo: pathlib.Path, codex_hooks: pathlib.Path,
) -> None:
    """The installer records each hook file; untouched hooks and wiring stay sealed."""
    before = json.loads((repo / ".ddw-installed.json").read_text())
    runner.install_verdict_tap(repo)
    manifest = json.loads((repo / ".ddw-installed.json").read_text())
    for name in ("pre-tool-use.sh", "post-write.sh"):
        key = f"codex:.codex/hooks/ddw/{name}"
        assert manifest[key] == runner._fingerprint(codex_hooks / name)
        assert manifest[key] != before[key]
    for name in ("session-start.sh", "pre-compact.sh"):
        key = f"codex:.codex/hooks/ddw/{name}"
        assert manifest[key] == before[key]
    assert manifest["codex:.codex/hooks.json"] == "unchanged-wiring"


def test_codex_installed_adapter_records_real_multi_path_refusals(tmp_path: pathlib.Path) -> None:
    """Exercise the installer and gate, not a second implementation of their wiring."""
    repo = runner.make_repo(pathlib.Path(ROOT), "codex", tmp_path / "fixture")
    hooks = repo / ".codex" / "hooks" / "ddw"
    assert (repo / ".codex" / "hooks.json").is_file()
    assert runner.install_verdict_tap(repo) == 2
    manifest = json.loads((repo / ".ddw-installed.json").read_text())
    for name in ("pre-tool-use.sh", "post-write.sh"):
        assert manifest[f"codex:.codex/hooks/ddw/{name}"] == runner._fingerprint(hooks / name)
    event = {"tool_name": "apply_patch", "tool_input": {"command":
             "*** Begin Patch\n*** Add File: src/a.py\n+x\n"
             "*** Add File: src/b.py\n+y\n*** End Patch\n"}}
    result = subprocess.run(["bash", str(hooks / "pre-tool-use.sh")], cwd=repo,
                            input=json.dumps(event), text=True, capture_output=True)
    assert result.returncode == 2
    decision = json.loads(result.stdout)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert result.stderr.strip(), "exit 2 must retain the adapter's blocking reason"
    assert _judge(repo, {"hook_refused": ["src/a.py", "src/b.py"]})[0] == runner.PASS


def test_codex_allow_and_polite_prose_are_not_hook_refusals(
    repo: pathlib.Path, codex_hooks: pathlib.Path,
) -> None:
    """Neither a successful hook nor a model refusal proves enforcement."""
    runner.install_verdict_tap(repo)
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "src/a.py"}})
    subprocess.run(["bash", str(codex_hooks / "post-write.sh")], input=payload,
                   text=True, cwd=repo, check=True, capture_output=True)
    verdict, _ = runner.judge_repo_state(
        {"expect": {"files_absent": ["src/a.py"], "hook_refused": ["src/a.py"],
                    "hook_refused_any": True}}, repo, _codex_events())
    assert verdict == runner.FAIL
    (repo / ".ddw-eval-verdicts.jsonl").unlink()
    assert runner.judge_repo_state(
        {"expect": {"hook_refused_any": True}}, repo, _codex_events())[0] == runner.FAIL


@pytest.mark.parametrize("hook, code", [("pre-tool-use.sh", 1), ("post-write.sh", 2)])
def test_codex_hook_errors_and_post_feedback_are_not_write_denials(
    repo: pathlib.Path, codex_hooks: pathlib.Path, hook: str, code: int,
) -> None:
    """Codex ignores non-2 pre errors, and post hooks cannot undo a write."""
    (codex_hooks / hook).write_text(f"#!/usr/bin/env bash\ncat >/dev/null\nexit {code}\n")
    runner.install_verdict_tap(repo)
    result = subprocess.run(
        ["bash", str(codex_hooks / hook)], input=json.dumps({
            "tool_name": "Write", "tool_input": {"file_path": "src/a.py"}}),
        text=True, cwd=repo, capture_output=True)
    assert result.returncode == code
    assert _judge(repo, {"hook_refused": ["src/a.py"], "hook_refused_any": True})[0] == runner.FAIL


@pytest.mark.parametrize("events", [_codex_events()[:-1], [{"type": "error", "message": "Failed"}]])
def test_codex_behavioral_parse_failure_is_error(
    repo: pathlib.Path, codex_hooks: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
    events: list[dict],
) -> None:
    """A successful process exit still cannot make a broken turn judgeable."""
    monkeypatch.setattr(runner, "sh", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, _jsonl(events), ""))
    monkeypatch.setenv("CODEX_HOME", str(repo.parent / "isolated-codex-home"))
    scenario = {"given": {}, "when": {"turns": ["First turn"]}, "expect": {}}
    assert runner.run_behavioral(scenario, pathlib.Path(ROOT), repo, ["codex"], None)[0] == runner.ERROR


def test_codex_behavioral_scenario_without_turns_is_not_green(repo: pathlib.Path) -> None:
    """No subprocess or parser runs for an empty scenario, so guard that boundary too."""
    scenario = {"given": {}, "when": {"turns": []}, "expect": {}}
    verdict, detail = runner.run_behavioral(scenario, pathlib.Path(ROOT), repo, ["codex"], None)
    assert verdict == runner.ERROR and detail.startswith("the agent did not complete a turn")


def test_codex_behavioral_resume_cannot_silently_start_a_different_thread(
    repo: pathlib.Path, codex_hooks: pathlib.Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codex can start a new thread when a requested resume target is absent."""
    changed = [{"type": "thread.started", "thread_id": "other"}, *_codex_events()[1:]]
    outputs = iter([_jsonl(_codex_events()), _jsonl(changed)])
    monkeypatch.setattr(runner, "sh", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, next(outputs), ""))
    monkeypatch.setenv("CODEX_HOME", str(repo.parent / "isolated-codex-home"))
    scenario = {"given": {}, "when": {"turns": ["First turn", "Second turn"]}, "expect": {}}
    verdict, detail = runner.run_behavioral(scenario, pathlib.Path(ROOT), repo, ["codex"], None)
    assert verdict == runner.ERROR and "different thread" in detail


def test_codex_behavioral_turns_execute_in_the_fixture_and_resume(
    repo: pathlib.Path, codex_hooks: pathlib.Path, tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fake binary exercises subprocess cwd/env and both real command builds."""
    binary = tmp_path / "codex"
    binary.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        "with open('calls.jsonl', 'a') as log:\n"
        "    log.write(json.dumps({'argv': sys.argv[1:], 'cwd': os.getcwd(), "
        "'pwd': os.environ['PWD'], 'codex_home': os.environ['CODEX_HOME']}) + '\\n')\n"
        f"print({_jsonl(_codex_events())!r}, end='')\n")
    binary.chmod(0o755)
    codex_home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    scenario = {"given": {}, "when": {"turns": ["First turn", "Second turn"]},
                "expect": {"files_absent": ["src/a.py"]}}
    verdict, _ = runner.run_behavioral(scenario, pathlib.Path(ROOT), repo, [str(binary)], None)
    assert verdict == runner.PASS
    calls = [json.loads(line) for line in (repo / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 2
    for call in calls:
        assert call["cwd"] == call["pwd"] == str(repo.resolve())
        assert call["codex_home"] == str(codex_home)
    assert "resume" not in calls[0]["argv"]
    assert calls[1]["argv"][-3:] == ["--", "thread-123", "Second turn"]
    assert "--sandbox" not in calls[1]["argv"] and "--cd" not in calls[1]["argv"]
    assert json.loads((repo.parent / "transcript.json").read_text()) == [_codex_events()] * 2


@pytest.mark.parametrize("stdout", [
    _jsonl(_codex_events()[:-1]), _jsonl(_codex_events()[1:]), "not JSON\n",
    _jsonl([{"type": "error", "message": "Failed"}]),
    _jsonl([{"type": "turn.failed", "error": {"message": "Failed"}}]),
])
def test_control_rejects_codex_parse_errors_instead_of_claiming_a_red(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    stdout: str,
) -> None:
    """Execute control-mode classification; parser failures are harness failures."""
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "one.yaml").write_text("id: one\nkind: behavioral\ngiven: {}\n")
    _, _, error = runner.agent_turn_read("codex", stdout)
    monkeypatch.setattr(runner, "run_one", lambda *args, **kwargs: runner.Result(
        "one", "behavioral", runner.ERROR, error))
    monkeypatch.setattr(sys, "argv", ["runner.py", "--repo", ROOT, "--scenarios",
                                    str(scenarios), "--control"])
    assert runner.main() == 1
    assert "the control proved nothing" in capsys.readouterr().out
