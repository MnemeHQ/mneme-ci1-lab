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
    assert {"path": ".mneme/project_memory.json", "reason": "deleted"} in out2["skipped"]


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
