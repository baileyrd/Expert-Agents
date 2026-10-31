"""Grade a developer's fix-bench runs by executing tests. Runs git and cargo.

For each stored run, in a scratch worktree of the target clone at the case's base:

- **fixed**: the run's changed files applied, then the held-back test files from the
  real fix on top; the case's test command passes.
- **red_at_base**: only the run's own changed test files (under a `tests/` directory)
  applied to the base; the case's test crate fails to pass with them. `None` when the
  run added no such file (its tests may live in `#[cfg(test)]` modules, which cannot be
  separated from the fix).
- **suite_green**: with the run's changes alone, `cargo test -p <crate>` passes.

A run is **solved** when it is fixed and suite_green. Eval-Kit's trace keeps the text of
each file the run added or changed (not deletions, not files over 200 kB); a file it
could not keep makes the run unreconstructable and it is reported as such.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from bench.fix.model import FixCase

SKIP_TOP = {"target", ".git", ".claude"}


@dataclass
class Grade:
    case: str
    run: int
    fixed: bool | None = None
    red_at_base: bool | None = None
    suite_green: bool | None = None
    changed: list[str] = field(default_factory=list)
    note: str = ""

    @property
    def solved(self) -> bool:
        return bool(self.fixed and self.suite_green)


def source_files(files: list) -> tuple[dict[str, str], list[str]]:
    """The run's changed source files by path, and the paths whose text was not kept."""
    kept, lost = {}, []
    for rel, text in files:
        parts = rel.split("/")
        if parts[0] in SKIP_TOP or "target" in parts:
            continue
        if text is None:
            lost.append(rel)
        else:
            kept[rel] = text
    return kept, lost


def own_tests(files: dict[str, str]) -> dict[str, str]:
    return {p: t for p, t in files.items() if "/tests/" in f"/{p}"}


def grade_run(case: FixCase, run: int, files: list, clone: Path, work: Path,
              crate: str, timeout: int) -> Grade:
    g = Grade(case.id, run)
    kept, lost = source_files(files)
    g.changed = sorted(kept)
    if lost:
        g.note = f"unreconstructable: {', '.join(lost)}"
        return g
    if not kept:
        g.note = "no source changes"
        g.fixed = g.suite_green = False
        return g
    tree = work / f"{case.id.split('/')[1]}-{run}"
    env = {**os.environ, "CARGO_TARGET_DIR": str(work / f"target-{case.id.split('/')[1]}")}
    with _worktree(clone, case.base, tree):
        tests = own_tests(kept)
        if tests:
            _write(tree, tests)
            g.red_at_base = not _ok(["cargo", "test", "-p", crate], tree, env, timeout)
            _git(tree, "checkout", "-q", "--", ".")
            _git(tree, "clean", "-qfd")
        _write(tree, kept)
        g.suite_green = _ok(["cargo", "test", "-p", crate], tree, env, timeout)
        _write(tree, {p: _git(clone, "show", f"{case.fix}:{p}") for p in case.held_back})
        g.fixed = _ok(list(case.test), tree, env, timeout)
    return g


def check_case(case: FixCase, clone: Path, work: Path, timeout: int) -> tuple[bool, bool]:
    """Is the case valid? Held-back tests fail at the base and pass at the fix."""
    tree = work / f"check-{case.id.split('/')[1]}"
    env = {**os.environ, "CARGO_TARGET_DIR": str(work / f"target-{case.id.split('/')[1]}")}
    with _worktree(clone, case.base, tree):
        _write(tree, {p: _git(clone, "show", f"{case.fix}:{p}") for p in case.held_back})
        red = not _ok(list(case.test), tree, env, timeout)
        _git(tree, "checkout", "-q", case.fix, "--", ".")
        green = _ok(list(case.test), tree, env, timeout)
    return red, green


def store_runs(store: Path, variant: str) -> dict[tuple[str, int], list]:
    """Each run's changed files, latest per case and sample (Eval-Kit's last line wins)."""
    out: dict[tuple[str, int], list] = {}
    for line in store.read_text().splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "verdict" or record.get("variant") != variant:
            continue
        out[(record["case"], int(record["sample"]) + 1)] = record["trace"].get("files") or []
    return out


class _worktree:
    def __init__(self, clone: Path, sha: str, path: Path):
        self.clone, self.sha, self.path = clone, sha, path

    def __enter__(self) -> Path:
        if self.path.exists():
            self._remove()
        _git(self.clone, "worktree", "add", "-q", "--detach", str(self.path), self.sha)
        return self.path

    def __exit__(self, *exc: object) -> None:
        self._remove()

    def _remove(self) -> None:
        subprocess.run(["git", "worktree", "remove", "--force", str(self.path)], cwd=self.clone,
                       capture_output=True, check=False)
        shutil.rmtree(self.path, ignore_errors=True)


def _write(tree: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = tree / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")


def _ok(argv: list[str], cwd: Path, env: dict[str, str], timeout: int) -> bool:
    try:
        return subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                              timeout=timeout, check=False).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout
