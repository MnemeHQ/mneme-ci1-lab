"""Regression tests for the gate workflow's failure reporting.

The "Evaluate" and "Gate decision" step scripts are taken from
.github/workflows/ci1-trusted-gate.yml and run the way GitHub runs
``shell: bash`` (``bash --noprofile --norc -eo pipefail``), against a stub
``base/gate/ci1_gate.py``. Before the fix, ``-e`` stopped the Evaluate step on
the gate's exit 1, so the exit code and job summary were never written.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci1-trusted-gate.yml"

STUB = r'''
import json, os, sys
args = sys.argv[1:]
report = args[args.index("--report") + 1]
mode = os.environ["STUB_MODE"]
def write(passed, verdict, problems):
    with open(report, "w", encoding="utf-8") as fh:
        json.dump({"verdict": verdict, "passed": passed, "problems": problems,
                   "base_sha": "b" * 40, "head_sha": "h" * 40,
                   "workspace": {"memory": {"blob": "m" * 40}, "refused": []},
                   "results": [{"path": "docs/x.md",
                                "classification": "VERIFIED_PASS" if passed else "VIOLATION"}]}, fh)
if mode == "pass":
    write(True, "PASS", []); sys.exit(0)
if mode == "violation":
    write(False, "BLOCK", ["docs/x.md: VIOLATION (FAIL)"]); sys.exit(1)
if mode == "crash":
    sys.exit(2)                       # no report at all
if mode == "exit0_report_says_block":
    write(False, "BLOCK", ["contradiction"]); sys.exit(0)
if mode == "exit0_no_report":
    sys.exit(0)
if mode == "exit0_garbage_report":
    open(report, "w").write("{not json"); sys.exit(0)
raise SystemExit(f"unknown mode {mode}")
'''


def _bash() -> str | None:
    for cand in (os.environ.get("CI1_BASH"), r"C:\Program Files\Git\bin\bash.exe",
                 None if os.name == "nt" else shutil.which("bash")):
        if cand and Path(cand).exists():
            return cand
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="bash not available")


def _step(name: str) -> str:
    wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    [step] = [s for s in wf["jobs"]["gate"]["steps"] if s["name"] == name]
    assert step.get("shell") == "bash"
    return step["run"]


def _posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def _env(tmp_path: Path, mode: str, gate_exit: str | None = None) -> dict:
    rt = tmp_path / "runner-temp"
    rt.mkdir(exist_ok=True)
    env = {**os.environ,
           "RUNNER_TEMP": _posix(rt),
           "GITHUB_OUTPUT": _posix(tmp_path / "github_output"),
           "GITHUB_STEP_SUMMARY": _posix(tmp_path / "step_summary.md"),
           "GATE_PYTHON": _posix(Path(sys.executable)),
           "BASE_SHA": "b" * 40, "HEAD_SHA": "h" * 40,
           "EVALUATOR_PYTHON": "/unused/by/stub", "STUB_MODE": mode}
    if gate_exit is not None:
        env["GATE_EXIT"] = gate_exit
    return env


def _run(tmp_path: Path, script: str, env: dict) -> subprocess.CompletedProcess:
    (tmp_path / "base" / "gate").mkdir(parents=True, exist_ok=True)
    (tmp_path / "base" / "gate" / "ci1_gate.py").write_text(STUB, encoding="utf-8")
    path = tmp_path / "step.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", _posix(path)],
                          cwd=tmp_path, env=env, capture_output=True, text=True)


def _evaluate(tmp_path: Path, mode: str) -> tuple[subprocess.CompletedProcess, dict, str]:
    proc = _run(tmp_path, _step("Evaluate the PR in the trusted workspace"), _env(tmp_path, mode))
    outputs = dict(line.split("=", 1) for line in
                   (tmp_path / "github_output").read_text().splitlines() if "=" in line) \
        if (tmp_path / "github_output").exists() else {}
    summary = (tmp_path / "step_summary.md").read_text(encoding="utf-8") \
        if (tmp_path / "step_summary.md").exists() else ""
    return proc, outputs, summary


def _decide(tmp_path: Path, gate_exit: str | None, mode: str = "n/a") -> subprocess.CompletedProcess:
    return _run(tmp_path, _step("Gate decision"), _env(tmp_path, mode, gate_exit))


# ── Evaluate always records the exit code and writes a summary ───────────────

def test_passing_gate_is_recorded_and_summarized(tmp_path):
    proc, out, summary = _evaluate(tmp_path, "pass")
    assert proc.returncode == 0, proc.stderr
    assert out["exit"] == "0"
    assert "Verdict: **PASS**" in summary and "VERIFIED_PASS" in summary


def test_violation_is_recorded_and_summarized_not_cut_short(tmp_path):
    """The V1 defect: exit 1 from the gate must not stop the step under -e."""
    proc, out, summary = _evaluate(tmp_path, "violation")
    assert proc.returncode == 0, proc.stderr
    assert out["exit"] == "1"
    assert "Verdict: **BLOCK**" in summary and "VIOLATION" in summary


def test_crash_without_report_is_recorded_and_summarized(tmp_path):
    proc, out, summary = _evaluate(tmp_path, "crash")
    assert proc.returncode == 0, proc.stderr
    assert out["exit"] == "2"
    assert "No gate report was produced" in summary


# ── Gate decision enforces, and fails closed ─────────────────────────────────

def test_decision_passes_only_a_clean_pass(tmp_path):
    _evaluate(tmp_path, "pass")
    assert _decide(tmp_path, "0").returncode == 0


@pytest.mark.parametrize("mode,gate_exit", [
    ("violation", "1"),
    ("crash", "2"),
    ("exit0_report_says_block", "0"),   # exit code and report disagree
    ("exit0_no_report", "0"),           # missing report
    ("exit0_garbage_report", "0"),      # unreadable report
    ("pass", ""),                       # no recorded exit code
    ("pass", None),                     # GATE_EXIT unset
])
def test_decision_blocks_everything_else(tmp_path, mode, gate_exit):
    _evaluate(tmp_path, mode)
    proc = _decide(tmp_path, gate_exit, mode)
    assert proc.returncode == 1, (proc.stdout, proc.stderr)
    assert "::error::" in proc.stdout
