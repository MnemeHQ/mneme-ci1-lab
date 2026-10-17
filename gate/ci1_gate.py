#!/usr/bin/env python3
"""CI1.3 T1 trusted merge gate: evaluate a pull request without running its code.

Runs from the BASE checkout of this repository (a ``pull_request_target``
workflow), never from the PR. Charter: MnemeHQ/mneme
``docs/plans/2026-10-17-ci1-3-isolated-merge-blocking-experiment-charter.md``
section 3 (T1 design details).

1. Create a fresh, empty trusted workspace ``ws/``.
2. Write the trusted decision memory to ``ws/.mneme/project_memory.json`` from
   the base commit's blob (``git cat-file``), never through a PR-controlled
   path. ``ws/`` is the ADR-020 policy root.
3. List the PR's changes with trusted git (merge-base..head, ``--raw``) and
   materialize each added or modified file into ``ws/`` as a regular file
   from its git blob. The PR head is fetched into the object store but never
   checked out, so a PR cannot plant a symlink, special file or path the gate
   later follows.
4. Refuse, and record as UNEVALUATED, any path that is not a regular blob,
   is absolute, contains ``..`` or a backslash, sits under ``.mneme/``, or
   collides with an entry the gate already wrote.
5. Run the pinned evaluator on every materialized file (strict mode,
   ``--json``, a fixed query) and classify each result. Exit codes are never
   read alone: the payload must be coherent with the exit code and the
   requested mode (CI1.2 / C1 ``assess_verdict`` rules).
6. Write a JSON report. Exit 0 only when every evaluated file is
   VERIFIED_PASS and nothing was refused (lab policy, not product policy:
   UNEVALUATED blocks here; ADR-033 is undecided).

Standard library only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

CONTRACT = "mneme.check/v1"
REQUESTED_MODE = "strict"
STRICT_EXIT = {"PASS": 0, "WARN": 1, "FAIL": 2}
# Literal-tier rules are query-independent (ADR-017). A fixed query keeps the
# PR title and other PR-controlled text out of evaluation (CI1.1 S7).
QUERY = "ci1 lab trusted gate"
MEMORY_REL = ".mneme/project_memory.json"
REGULAR_MODES = {"100644", "100755"}
EVAL_TIMEOUT_S = 120


class GateError(RuntimeError):
    """The gate itself could not run; the result is UNEVALUATED."""


# ── trusted git ──────────────────────────────────────────────────────────────

def git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if proc.returncode != 0:
        raise GateError(f"git {' '.join(args)} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def parse_raw_diff(raw: bytes) -> list[dict[str, str]]:
    """Parse ``git diff --raw -z --no-renames`` output.

    Each record is ``:<old mode> <new mode> <old sha> <new sha> <status>\\0<path>\\0``.
    """
    entries = []
    parts = raw.split(b"\0")
    i = 0
    while i < len(parts) and parts[i]:
        meta = parts[i].decode()
        if not meta.startswith(":") or i + 1 >= len(parts):
            raise GateError(f"unexpected raw diff record: {meta!r}")
        _old_mode, new_mode, _old_sha, new_sha, status = meta[1:].split(" ")
        path = parts[i + 1].decode("utf-8", errors="surrogateescape")
        entries.append({"status": status[0], "mode": new_mode, "blob": new_sha, "path": path})
        i += 2
    return entries


def changed_entries(repo: Path, base_sha: str, head_sha: str) -> tuple[str, list[dict[str, str]]]:
    merge_base = git(repo, "merge-base", base_sha, head_sha).decode().strip()
    raw = git(repo, "diff", "--raw", "-z", "--no-renames", "--no-ext-diff", merge_base, head_sha)
    return merge_base, parse_raw_diff(raw)


# ── trusted workspace ────────────────────────────────────────────────────────

def refusal_reason(path: str, mode: str) -> str | None:
    if mode not in REGULAR_MODES:
        kind = {"120000": "symlink", "160000": "submodule"}.get(mode, f"mode {mode}")
        return f"not a regular file ({kind})"
    if not path or path.startswith("/") or "\\" in path or "\0" in path:
        return "not a normalized relative path"
    parts = path.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return "path contains an empty, '.' or '..' component"
    if parts[0] == ".mneme":
        return "policy path: PR changes never reach the trusted policy location"
    return None


def _ensure_dirs(ws: Path, rel_parts: list[str]) -> Path:
    """Create each parent directory inside ``ws`` without following anything."""
    cur = ws
    for part in rel_parts:
        cur = cur / part
        try:
            os.mkdir(cur)
        except FileExistsError:
            st = os.lstat(cur)
            if not os.path.isdir(cur) or os.path.islink(cur) or (st.st_mode & 0o170000) != 0o040000:
                raise GateError(f"collides with an existing non-directory entry: {cur.relative_to(ws)}")
    return cur


def write_exclusive(ws: Path, rel: str, data: bytes) -> Path:
    """Write ``data`` to ``ws/rel`` as a new regular file; never replace or follow."""
    parts = rel.split("/")
    parent = _ensure_dirs(ws, parts[:-1])
    target = parent / parts[-1]
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(target, flags, 0o644)
    except FileExistsError:
        raise GateError(f"collides with an entry the gate already wrote: {rel}") from None
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    return target


def build_workspace(repo: Path, base_sha: str, head_sha: str, ws: Path) -> dict[str, Any]:
    os.mkdir(ws)  # must not exist: a fresh, gate-owned directory
    memory_blob = git(repo, "rev-parse", f"{base_sha}:{MEMORY_REL}").decode().strip()
    memory = git(repo, "cat-file", "blob", memory_blob)
    memory_path = write_exclusive(ws, MEMORY_REL, memory)
    merge_base, entries = changed_entries(repo, base_sha, head_sha)
    materialized, refused, skipped = [], [], []
    for e in entries:
        if e["status"] == "D":
            skipped.append({"path": e["path"], "reason": "deleted"})
            continue
        reason = refusal_reason(e["path"], e["mode"])
        if reason is None:
            try:
                data = git(repo, "cat-file", "blob", e["blob"])
                write_exclusive(ws, e["path"], data)
                materialized.append({"path": e["path"], "blob": e["blob"], "mode": e["mode"],
                                     "sha256": hashlib.sha256(data).hexdigest()})
                continue
            except GateError as exc:
                reason = str(exc)
        refused.append({"path": e["path"], "mode": e["mode"], "reason": reason})
    return {
        "merge_base": merge_base,
        "memory": {"path": MEMORY_REL, "blob": memory_blob, "source_commit": base_sha,
                   "sha256": hashlib.sha256(memory).hexdigest(), "written_to": str(memory_path)},
        "materialized": materialized, "refused": refused, "skipped": skipped,
    }


# ── evaluation and classification ────────────────────────────────────────────

def classify(stdout: str, exit_code: int, requested_mode: str = REQUESTED_MODE) -> tuple[str, str]:
    """Return (classification, reason). Only a coherent payload is a verdict."""
    try:
        payload = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return "UNEVALUATED", f"no single parseable JSON payload on stdout (exit {exit_code})"
    if not isinstance(payload, dict):
        return "UNEVALUATED", "payload is not an object"
    if payload.get("schema") != CONTRACT:
        return "UNEVALUATED", f"schema {payload.get('schema')!r} is not {CONTRACT}"
    if payload.get("mode") != requested_mode:
        return "UNEVALUATED", f"reported mode {payload.get('mode')!r} != requested {requested_mode!r}"
    if payload.get("evaluation_complete") is not True:
        return "UNEVALUATED", "evaluation_complete is not true"
    verdict = payload.get("verdict")
    if verdict not in STRICT_EXIT:
        return "UNEVALUATED", f"unknown verdict {verdict!r}"
    violations = payload.get("violations")
    if not isinstance(violations, list):
        return "UNEVALUATED", "violations is not a list"
    if verdict == "PASS" and violations:
        return "UNEVALUATED", "PASS verdict carries violations"
    if verdict == "FAIL" and not violations:
        return "UNEVALUATED", "FAIL verdict carries no violations"
    if exit_code != STRICT_EXIT[verdict]:
        return "UNEVALUATED", f"exit {exit_code} contradicts verdict {verdict} in strict mode"
    return {"PASS": "VERIFIED_PASS", "FAIL": "VIOLATION", "WARN": "WARN"}[verdict], verdict


def clean_env() -> dict[str, str]:
    drop = {"PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE"}
    return {k: v for k, v in os.environ.items() if k.upper() not in drop}


def evaluate(venv_python: Path, ws: Path, files: list[dict[str, Any]], empty_dir: Path) -> list[dict[str, Any]]:
    memory = ws / MEMORY_REL
    results = []
    for f in files:
        target = ws / f["path"]
        cmd = [str(venv_python), "-I", "-m", "mneme.cli", "check",
               "--memory", str(memory), "--input", str(target), "--target-path", str(target),
               "--query", QUERY, "--mode", REQUESTED_MODE, "--adr-dir", str(empty_dir), "--json"]
        try:
            proc = subprocess.run(cmd, cwd=empty_dir, env=clean_env(), capture_output=True,
                                  text=True, encoding="utf-8", errors="replace", timeout=EVAL_TIMEOUT_S)
            cls, reason = classify(proc.stdout, proc.returncode)
            record = {"exit": proc.returncode, "stdout": proc.stdout, "stderr_tail": proc.stderr[-2000:]}
        except subprocess.TimeoutExpired:
            cls, reason, record = "UNEVALUATED", f"timed out after {EVAL_TIMEOUT_S}s", {}
        results.append({"path": f["path"], "classification": cls, "reason": reason, **record})
    return results


def decide(workspace: dict[str, Any], results: list[dict[str, Any]]) -> tuple[bool, list[str]]:
    problems = [f"refused {r['path']}: {r['reason']}" for r in workspace["refused"]]
    problems += [f"{r['path']}: {r['classification']} ({r['reason']})"
                 for r in results if r["classification"] != "VERIFIED_PASS"]
    return not problems, problems


# ── entry point ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, required=True, help="base checkout holding both commits")
    ap.add_argument("--base-sha", required=True)
    ap.add_argument("--head-sha", required=True)
    ap.add_argument("--workspace", type=Path, required=True, help="must not exist yet")
    ap.add_argument("--venv-python", type=Path, required=True, help="pinned evaluator interpreter")
    ap.add_argument("--empty-dir", type=Path, required=True, help="empty cwd / ADR dir for the evaluator")
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args(argv)

    report: dict[str, Any] = {"base_sha": args.base_sha, "head_sha": args.head_sha,
                              "requested_mode": REQUESTED_MODE, "query": QUERY}
    try:
        args.empty_dir.mkdir(parents=True, exist_ok=False)
        workspace = build_workspace(args.repo, args.base_sha, args.head_sha, args.workspace)
        results = evaluate(args.venv_python, args.workspace, workspace["materialized"], args.empty_dir)
        passed, problems = decide(workspace, results)
        report.update({"workspace": workspace, "results": results})
    except GateError as exc:
        passed, problems = False, [f"gate could not evaluate: {exc}"]
    report.update({"passed": passed, "problems": problems,
                   "verdict": "PASS" if passed else ("UNEVALUATED" if any("could not" in p for p in problems)
                                                     else "BLOCK")})
    args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for p in problems:
        print(f"::error::{p}")
    print(f"[ci1-gate] verdict: {report['verdict']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
