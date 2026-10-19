"""Offline tests for gate/ci1_gate.py (no network, no GitHub).

Real temporary git repositories exercise the trusted workspace: PR content is
read from git blobs only, and symlinks, submodules, traversal and policy
paths are refused. Classification is tested on stubbed CLI output.
"""
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci1_gate", ROOT / "gate" / "ci1_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

TRUSTED_MEMORY = (ROOT / ".mneme" / "project_memory.json").read_bytes()
FORBIDDEN = b"Run `install legacy-client` to get started.\n"


def _git(repo, *args, input=None):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          input=input).stdout.decode().strip()


def _commit(repo, msg):
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-q", "--allow-empty", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _write(repo, rel, data):
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    _git(repo, "add", rel)


def _add_index_entry(repo, rel, mode, data=b"target"):
    sha = _git(repo, "hash-object", "-w", "--stdin", input=data)
    _git(repo, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{rel}")


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "core.autocrlf", "false")
    _write(r, ".mneme/project_memory.json", TRUSTED_MEMORY)
    _write(r, "README.md", b"lab\n")
    base = _commit(r, "base")
    _git(r, "checkout", "-q", "-b", "pr")
    return r, base


# ── workspace ────────────────────────────────────────────────────────────────

def test_workspace_holds_trusted_memory_and_materialized_files(repo, tmp_path):
    r, base = repo
    _write(r, "docs/guide.md", FORBIDDEN)
    head = _commit(r, "pr")
    ws = tmp_path / "ws"
    out = gate.build_workspace(r, base, head, ws)
    assert (ws / ".mneme/project_memory.json").read_bytes() == TRUSTED_MEMORY
    assert (ws / "docs/guide.md").read_bytes() == FORBIDDEN
    assert [m["path"] for m in out["materialized"]] == ["docs/guide.md"]
    assert out["refused"] == []
    assert out["memory"]["sha256"] == hashlib.sha256(TRUSTED_MEMORY).hexdigest()


def test_pr_edit_or_deletion_of_memory_never_reaches_the_workspace(repo, tmp_path):
    r, base = repo
    _write(r, ".mneme/project_memory.json", b'{"permissive": true}\n')
    head = _commit(r, "weaken policy")
    out = gate.build_workspace(r, base, head, tmp_path / "ws")
    assert (tmp_path / "ws/.mneme/project_memory.json").read_bytes() == TRUSTED_MEMORY
    assert out["refused"][0]["path"] == ".mneme/project_memory.json"
    assert "policy path" in out["refused"][0]["reason"]

    _git(r, "rm", "-q", ".mneme/project_memory.json")
    head2 = _commit(r, "delete policy")
    out2 = gate.build_workspace(r, base, head2, tmp_path / "ws2")
    assert (tmp_path / "ws2/.mneme/project_memory.json").read_bytes() == TRUSTED_MEMORY
    refused = {x["path"]: x["reason"] for x in out2["refused"]}
    assert "policy path deleted" in refused[".mneme/project_memory.json"]
    assert out2["skipped"] == []


def _run_gate(r, base, head, tmp_path):
    report = tmp_path / "report.json"
    code = gate.main(["--repo", str(r), "--base-sha", base, "--head-sha", head,
                      "--workspace", str(tmp_path / "ws"), "--venv-python", sys.executable,
                      "--empty-dir", str(tmp_path / "empty"), "--report", str(report)])
    return code, json.loads(report.read_text(encoding="utf-8"))


def test_deletion_only_pr_removing_the_trusted_memory_cannot_pass(repo, tmp_path):
    """Regression: a PR whose only change deletes .mneme/project_memory.json
    must block. It has no file to evaluate, so it would otherwise pass and
    could remove the policy every later PR is judged against."""
    r, base = repo
    _git(r, "rm", "-q", ".mneme/project_memory.json")
    head = _commit(r, "delete trusted memory only")
    code, out = _run_gate(r, base, head, tmp_path)
    assert code == 1 and out["passed"] is False and out["verdict"] == "BLOCK"
    assert out["results"] == []  # nothing was evaluated, and it still blocks
    assert any("policy path deleted" in p for p in out["problems"])


def test_deleting_the_whole_policy_directory_cannot_pass(repo, tmp_path):
    r, base = repo
    _write(r, ".mneme/extra.json", b"{}\n")
    base2 = _commit(r, "second policy file on base")
    _git(r, "rm", "-q", "-r", ".mneme")
    head = _commit(r, "delete .mneme/")
    code, out = _run_gate(r, base2, head, tmp_path)
    assert code == 1
    assert {x["path"] for x in out["workspace"]["refused"]} == {".mneme/project_memory.json", ".mneme/extra.json"}


def test_ordinary_deletions_are_not_evaluated_and_do_not_block(repo, tmp_path):
    r, base = repo
    _git(r, "rm", "-q", "README.md")
    head = _commit(r, "delete readme")
    code, out = _run_gate(r, base, head, tmp_path)
    assert code == 0 and out["passed"] is True
    assert out["workspace"]["skipped"] == [{"path": "README.md", "reason": "deleted"}]


@pytest.mark.parametrize("rel,mode,needle", [
    ("docs/link.md", "120000", "symlink"),
    (".mneme", "120000", "symlink"),
    ("vendor/sub", "160000", "submodule"),
])
def test_non_regular_entries_are_refused_not_followed(repo, tmp_path, rel, mode, needle):
    r, base = repo
    if rel == ".mneme":
        _git(r, "rm", "-q", "-r", ".mneme")
    if mode == "160000":
        sha = "0" * 39 + "1"
        _git(r, "update-index", "--add", "--cacheinfo", f"{mode},{sha},{rel}")
    else:
        _add_index_entry(r, rel, mode, b"../../outside")
    _write(r, "docs/guide.md", FORBIDDEN)
    head = _commit(r, "planted entry")
    ws = tmp_path / "ws"
    out = gate.build_workspace(r, base, head, ws)
    refused = {x["path"]: x["reason"] for x in out["refused"]}
    assert needle in refused[rel]
    assert not os.path.islink(ws / rel)
    assert (ws / ".mneme/project_memory.json").read_bytes() == TRUSTED_MEMORY
    assert "docs/guide.md" in [m["path"] for m in out["materialized"]]


def test_workspace_must_be_fresh(repo, tmp_path):
    r, base = repo
    (tmp_path / "ws").mkdir()
    with pytest.raises(FileExistsError):
        gate.build_workspace(r, base, base, tmp_path / "ws")


@pytest.mark.parametrize("path,mode,refused", [
    ("docs/a.md", "100644", False),
    ("scripts/run.sh", "100755", False),
    ("docs/../../etc/passwd", "100644", True),
    ("/abs.md", "100644", True),
    ("docs\\a.md", "100644", True),
    ("docs//a.md", "100644", True),
    ("./a.md", "100644", True),
    (".mneme/project_memory.json", "100644", True),
    ("docs/a.md", "120000", True),
    ("sub", "160000", True),
])
def test_refusal_reason(path, mode, refused):
    assert (gate.refusal_reason(path, mode) is not None) is refused


def test_raw_diff_parsing_keeps_modes_and_blobs():
    raw = (b":000000 100644 " + b"0" * 40 + b" " + b"a" * 40 + b" A\0docs/x.md\0"
           b":100644 120000 " + b"b" * 40 + b" " + b"c" * 40 + b" T\0link\0")
    assert gate.parse_raw_diff(raw) == [
        {"status": "A", "mode": "100644", "blob": "a" * 40, "path": "docs/x.md"},
        {"status": "T", "mode": "120000", "blob": "c" * 40, "path": "link"},
    ]


# ── classification ───────────────────────────────────────────────────────────

def _payload(**over):
    p = {"schema": "mneme.check/v1", "verdict": "PASS", "mode": "strict",
         "evaluation_complete": True, "applicability": [], "violations": [], "freshness": []}
    p.update(over)
    return json.dumps(p)


VIOLATION = [{"decision_id": "ADR-901", "rule_id": "r"}]


@pytest.mark.parametrize("stdout,code,expected", [
    (_payload(), 0, "VERIFIED_PASS"),
    (_payload(verdict="FAIL", violations=VIOLATION), 2, "VIOLATION"),
    (_payload(verdict="WARN", violations=VIOLATION), 1, "WARN"),
    (_payload(evaluation_complete=False), 2, "UNEVALUATED"),       # UNKNOWN applicability
    (_payload(evaluation_complete="true"), 0, "UNEVALUATED"),
    (_payload(mode="warn"), 0, "UNEVALUATED"),                     # requested-mode mismatch
    (_payload(), 2, "UNEVALUATED"),                                # PASS with FAIL exit
    (_payload(verdict="FAIL", violations=VIOLATION), 0, "UNEVALUATED"),
    (_payload(violations=VIOLATION), 0, "UNEVALUATED"),            # PASS carrying violations
    (_payload(verdict="FAIL"), 2, "UNEVALUATED"),                  # FAIL with no violations
    (_payload(schema="mneme.check/v0"), 0, "UNEVALUATED"),
    (_payload(verdict="MAYBE"), 0, "UNEVALUATED"),
    ("", 1, "UNEVALUATED"),                                        # malformed memory: traceback, exit 1
    ("", 2, "UNEVALUATED"),                                        # missing memory: exit 2
    ("not json", 0, "UNEVALUATED"),
    (_payload() + "\n" + _payload(), 0, "UNEVALUATED"),            # two payloads
    ("[]", 0, "UNEVALUATED"),
])
def test_classify(stdout, code, expected):
    assert gate.classify(stdout, code)[0] == expected


def test_decide_blocks_on_any_refusal_or_non_pass():
    ws_ok = {"refused": []}
    ok = [{"path": "a", "classification": "VERIFIED_PASS", "reason": "PASS"}]
    assert gate.decide(ws_ok, ok) == (True, [])
    assert gate.decide({"refused": [{"path": "l", "reason": "symlink"}]}, ok)[0] is False
    assert gate.decide(ws_ok, ok + [{"path": "b", "classification": "WARN", "reason": "WARN"}])[0] is False
    assert gate.decide(ws_ok, [])[0] is True  # nothing to evaluate: V0-style empty change passes


# ── end to end against a locally installed evaluator (skipped if absent) ─────

# The gate runs the evaluator with ``python -I``, so it must be importable in
# isolated mode. Point CI1_GATE_EVALUATOR_PYTHON at a venv holding the pinned
# wheel (the approved P2 build) to run these; otherwise they are skipped.
EVALUATOR = os.environ.get("CI1_GATE_EVALUATOR_PYTHON", sys.executable)


def _evaluator_available() -> bool:
    probe = subprocess.run([EVALUATOR, "-I", "-c", "import mneme.cli"], capture_output=True)
    return probe.returncode == 0


needs_mneme = pytest.mark.skipif(not _evaluator_available(),
                                 reason="no evaluator importable under python -I")


@needs_mneme
@pytest.mark.parametrize("rel,content,verdict,expect_pass", [
    ("docs/guide.md", b"Run `install current-client`.\n", "VERIFIED_PASS", True),
    ("docs/guide.md", FORBIDDEN, "VIOLATION", False),
    ("docs/history/old.md", FORBIDDEN, "VERIFIED_PASS", True),   # exclude selector
    ("src/notes.md", FORBIDDEN, "VERIFIED_PASS", True),          # no include match (coverage limit)
])
def test_end_to_end(repo, tmp_path, rel, content, verdict, expect_pass):
    r, base = repo
    _write(r, rel, content)
    head = _commit(r, "pr")
    report = tmp_path / "report.json"
    code = gate.main(["--repo", str(r), "--base-sha", base, "--head-sha", head,
                      "--workspace", str(tmp_path / "ws"), "--venv-python", EVALUATOR,
                      "--empty-dir", str(tmp_path / "empty"), "--report", str(report)])
    out = json.loads(report.read_text(encoding="utf-8"))
    assert out["results"][0]["classification"] == verdict, out["results"][0]
    assert (code == 0) is expect_pass and out["passed"] is expect_pass


@needs_mneme
def test_end_to_end_violation_with_policy_removal_still_blocks(repo, tmp_path):
    """V2: the PR removes the rule from its own memory and adds a violation."""
    r, base = repo
    weakened = json.loads(TRUSTED_MEMORY)
    weakened["decision_index"]["rules"] = []
    _write(r, ".mneme/project_memory.json", json.dumps(weakened).encode())
    _write(r, "docs/guide.md", FORBIDDEN)
    head = _commit(r, "violate and weaken")
    report = tmp_path / "report.json"
    code = gate.main(["--repo", str(r), "--base-sha", base, "--head-sha", head,
                      "--workspace", str(tmp_path / "ws"), "--venv-python", EVALUATOR,
                      "--empty-dir", str(tmp_path / "empty"), "--report", str(report)])
    out = json.loads(report.read_text(encoding="utf-8"))
    by_path = {x["path"]: x["classification"] for x in out["results"]}
    assert by_path["docs/guide.md"] == "VIOLATION"
    assert code == 1


# ── C1: trusted-surface refusal (charter docs/plans/2026-10-19-ci1-c1-lab-validation-charter.md) ──
#
# O1-O10 of the charter's section 5. The base below holds one file for each
# trusted-surface entry S1-S6 and S8; .mneme/ (S7) keeps its own tests above.

SURFACE_BASE = {
    ".github/workflows/ci1-trusted-gate.yml": b"name: gate\n",          # S1
    ".github/workflows/other.yml": b"name: other\n",                    # S2
    "gate/ci1_gate.py": b"# gate\n",                                    # S3
    "gate/evaluator-requirements.txt": b"# reqs\n",                     # S4
    "p2/build-constraints.txt": b"# constraints\n",                     # S5
    ".gitattributes": b"*.whl binary\n",                                # S6
    "policy/ruleset-main.proposed.json": b"{}\n",                       # S8
}
NEW_IN_SURFACE = [".github/workflows/new.yml", ".github/CODEOWNERS", "gate/new.py",
                  "gate/sub/deep.py", "p2/new.txt", "policy/new.json"]


@pytest.fixture
def surface_repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "core.autocrlf", "false")
    _write(r, ".mneme/project_memory.json", TRUSTED_MEMORY)
    _write(r, "README.md", b"lab\n")
    _write(r, "docs/existing.md", b"Run `install current-client`.\n")
    for rel, data in SURFACE_BASE.items():
        _write(r, rel, data)
    base = _commit(r, "base with trusted surface")
    _git(r, "checkout", "-q", "-b", "pr")
    return r, base


def _surface_refusals(out):
    return {x["path"]: x for x in out["refused"] if x.get("kind") == "trusted_surface"}


def _run_gate_with(evaluator, r, base, head, tmp_path):
    report = tmp_path / "report.json"
    gate.main(["--repo", str(r), "--base-sha", base, "--head-sha", head,
               "--workspace", str(tmp_path / "ws"), "--venv-python", evaluator,
               "--empty-dir", str(tmp_path / "empty"), "--report", str(report)])
    return json.loads(report.read_text(encoding="utf-8"))


@pytest.mark.parametrize("rel", sorted(SURFACE_BASE))
def test_c1_o1_modify_each_surface_file_is_refused(surface_repo, tmp_path, rel):
    r, base = surface_repo
    _write(r, rel, SURFACE_BASE[rel] + b"# weakened\n")
    head = _commit(r, f"modify {rel}")
    code, out = _run_gate(r, base, head, tmp_path)
    assert code == 1 and out["verdict"] == "BLOCK"
    assert _surface_refusals(out["workspace"])[rel]["reason"] == gate.SURFACE_REASON
    assert out["workspace"]["materialized"] == []


@pytest.mark.parametrize("rel", NEW_IN_SURFACE)
def test_c1_o1_add_to_surface_is_refused(surface_repo, tmp_path, rel):
    r, base = surface_repo
    _write(r, rel, b"new\n")
    head = _commit(r, f"add {rel}")
    code, out = _run_gate(r, base, head, tmp_path)
    assert code == 1 and rel in _surface_refusals(out["workspace"])


@pytest.mark.parametrize("rel", sorted(SURFACE_BASE))
def test_c1_o1_mode_change_on_surface_is_refused(surface_repo, tmp_path, rel):
    r, base = surface_repo
    _git(r, "update-index", "--chmod=+x", rel)
    head = _commit(r, f"chmod {rel}")
    assert _git(r, "diff", "--raw", "--no-renames", base, head).split()[1] == "100755"
    code, out = _run_gate(r, base, head, tmp_path)
    assert code == 1 and rel in _surface_refusals(out["workspace"])


@pytest.mark.parametrize("rel", sorted(SURFACE_BASE))
def test_c1_o1_delete_surface_file_is_refused(surface_repo, tmp_path, rel):
    r, base = surface_repo
    _git(r, "rm", "-q", rel)
    head = _commit(r, f"delete {rel}")
    code, out = _run_gate(r, base, head, tmp_path)
    assert code == 1 and out["verdict"] == "BLOCK"
    refused = _surface_refusals(out["workspace"])
    assert "trusted surface deleted" in refused[rel]["reason"]
    assert out["workspace"]["skipped"] == []


@pytest.mark.parametrize("src,dst,refused_paths", [
    ("docs/existing.md", "gate/moved.md", {"gate/moved.md"}),                                # into
    ("gate/ci1_gate.py", "docs/ci1_gate.py", {"gate/ci1_gate.py"}),                          # out of
    (".github/workflows/ci1-trusted-gate.yml", ".github/workflows/renamed.yml",              # within
     {".github/workflows/ci1-trusted-gate.yml", ".github/workflows/renamed.yml"}),
])
def test_c1_o2_rename_is_refused_on_either_side(surface_repo, tmp_path, src, dst, refused_paths):
    r, base = surface_repo
    _git(r, "mv", src, dst)
    head = _commit(r, f"rename {src} -> {dst}")
    out = gate.build_workspace(r, base, head, tmp_path / "ws")
    assert set(_surface_refusals(out)) == refused_paths
    assert gate.decide(out, [])[0] is False


def test_c1_o3_surface_change_alone_blocks_with_nothing_evaluated(surface_repo, tmp_path):
    r, base = surface_repo
    _write(r, "gate/ci1_gate.py", b"raise SystemExit(0)\n")
    head = _commit(r, "gate-only change")
    code, out = _run_gate(r, base, head, tmp_path)
    assert code == 1 and out["passed"] is False and out["verdict"] == "BLOCK"
    assert out["results"] == [] and out["workspace"]["materialized"] == []
    assert any("(trusted_surface)" in p for p in out["problems"])


@pytest.mark.parametrize("path,on_surface", [
    (".github/workflows/ci1-trusted-gate.yml", True),
    (".github/workflows/deep/x.yml", True),
    (".github/CODEOWNERS", True),
    ("gate/ci1_gate.py", True),
    ("gate/sub/deep.py", True),
    ("p2/build-constraints.txt", True),
    ("policy/ruleset-main.proposed.json", True),
    (".gitattributes", True),
    ("gate.md", False),                 # near misses
    ("gatekeeper/x.py", False),
    ("docs/gate/x.md", False),
    (".githubx/y", False),
    (".github", False),                 # the bare name is not a file under the directory
    ("policyx/a.json", False),
    ("p2.txt", False),
    ("docs/.gitattributes", False),
    ("tests/test_ci1_gate.py", False),  # not a gate input (charter section 3)
    ("README.md", False),
    ("docs/a.md", False),
])
def test_c1_o4_surface_matching_nested_and_near_misses(path, on_surface):
    assert gate.is_trusted_surface(path) is on_surface
    expected = ("trusted_surface", gate.SURFACE_REASON) if on_surface else None
    assert gate.refusal(path, "100644") == expected


def test_c1_o4_near_miss_paths_are_handled_as_before(surface_repo, tmp_path):
    r, base = surface_repo
    for rel in ("gate.md", "docs/gate/x.md", ".githubx/y"):
        _write(r, rel, b"text\n")
    head = _commit(r, "near misses")
    out = gate.build_workspace(r, base, head, tmp_path / "ws")
    assert out["refused"] == []
    assert sorted(m["path"] for m in out["materialized"]) == [".githubx/y", "docs/gate/x.md", "gate.md"]


def test_c1_o4_symlink_on_surface_keeps_the_mode_refusal(surface_repo, tmp_path):
    r, base = surface_repo
    _add_index_entry(r, "gate/link.py", "120000", b"../../outside")
    head = _commit(r, "symlink on surface")
    out = gate.build_workspace(r, base, head, tmp_path / "ws")
    assert {x["path"]: x["kind"] for x in out["refused"]} == {"gate/link.py": "mode"}


def test_c1_o9_every_base_file_the_workflow_reads_is_protected():
    """O9: the real gate workflow's base/ reads, and the workflow files
    themselves, are all on the trusted surface or the policy path."""
    import re
    wf = ROOT / ".github" / "workflows" / "ci1-trusted-gate.yml"
    reads = set(re.findall(r"\bbase/([A-Za-z0-9_.\-/]+[A-Za-z0-9_\-])", wf.read_text(encoding="utf-8")))
    assert {"gate/ci1_gate.py", "gate/evaluator-requirements.txt", "p2/build-constraints.txt"} <= reads
    for rel in sorted(reads):
        assert gate.is_trusted_surface(rel) or gate.is_policy_path(rel), rel
    for p in (ROOT / ".github").rglob("*"):
        if p.is_file():
            rel = p.relative_to(ROOT).as_posix()
            assert gate.is_trusted_surface(rel), rel
    for rel in (".gitattributes", "gate/ci1_gate.py", gate.MEMORY_REL):
        assert gate.is_trusted_surface(rel) or gate.is_policy_path(rel), rel


def test_c1_o10_refusals_carry_a_kind(surface_repo, tmp_path):
    r, base = surface_repo
    _write(r, "gate/ci1_gate.py", b"# edited\n")
    _write(r, ".mneme/project_memory.json", b"{}\n")
    _add_index_entry(r, "docs/link.md", "120000", b"../outside")
    head = _commit(r, "three kinds")
    out = gate.build_workspace(r, base, head, tmp_path / "ws")
    kinds = {x["path"]: x["kind"] for x in out["refused"]}
    assert kinds == {"gate/ci1_gate.py": "trusted_surface", ".mneme/project_memory.json": "policy",
                     "docs/link.md": "mode"}
    assert all(set(x) == {"path", "mode", "kind", "reason"} for x in out["refused"])
    problems = gate.decide(out, [])[1]
    assert "refused gate/ci1_gate.py (trusted_surface): " + gate.SURFACE_REASON in problems


@needs_mneme
@pytest.mark.parametrize("content,verdict,expect_pass", [
    (b"Run `install current-client`.\n", "VERIFIED_PASS", True),   # O5 compliant control
    (FORBIDDEN, "VIOLATION", False),                               # O6 violation regression
])
def test_c1_o5_o6_docs_control_and_violation_unchanged(surface_repo, tmp_path, content, verdict, expect_pass):
    r, base = surface_repo
    _write(r, "docs/guide.md", content)
    head = _commit(r, "docs change")
    out = _run_gate_with(EVALUATOR, r, base, head, tmp_path)
    assert out["workspace"]["refused"] == []
    assert out["results"][0]["classification"] == verdict, out["results"][0]
    assert out["passed"] is expect_pass and out["verdict"] == ("PASS" if expect_pass else "BLOCK")


@needs_mneme
def test_c1_o7_surface_change_and_violation_are_both_reported(surface_repo, tmp_path):
    r, base = surface_repo
    _write(r, ".github/workflows/ci1-trusted-gate.yml", b"name: gate\n# exit 0\n")
    _write(r, "docs/guide.md", FORBIDDEN)
    head = _commit(r, "surface change plus violation")
    out = _run_gate_with(EVALUATOR, r, base, head, tmp_path)
    assert out["verdict"] == "BLOCK"
    assert ".github/workflows/ci1-trusted-gate.yml" in _surface_refusals(out["workspace"])
    [res] = out["results"]
    assert res["path"] == "docs/guide.md" and res["classification"] == "VIOLATION"
    assert "kind" not in res  # O10: a VIOLATION record is not a refusal record
