"""Grade a developer's fix-bench runs by executing tests. Runs git and cargo.

For each stored run, in a scratch worktree of the target clone at the case's base:

- **fixed**: the run's changed files applied, then the held-back test files from the
  real fix on top; the case's test command passes.
- **red_at_base**: only the run's own changed test files (under a `tests/` directory)
  applied to the base; the developer's suite command fails with them. `None` when the
  run added no such file (its tests may live in `#[cfg(test)]` modules, which cannot be
  separated from the fix).
- **suite_green**: with the run's changes alone, the developer's suite command passes.

A run is **solved** when it is fixed and suite_green. Eval-Kit's trace keeps the text of
each file the run added or changed (not deletions, not files over 200 kB); a file it
could not keep makes the run unreconstructable and it is reported as such.

Every cargo command runs inside the sandbox in `bench.fix.sandbox`: a run's files are
model output, and its build scripts and tests are code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from bench.fix import sandbox
from bench.fix.model import FixCase

SKIP_TOP = {"target", ".git", ".claude"}


@dataclass
class Grade:
    case: str
    run: int
    key: str = ""  # Eval-Kit's content key of the graded run (prompt, model, settings, case)
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


def grade_run(case: FixCase, run: int, key: str, files: list, clone: Path, work: Path,
              suite: list[str], timeout: int) -> Grade:
    g = Grade(case.id, run, key)
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
    target = _target(work, case)
    with _worktree(clone, case.base, tree):
        tests = own_tests(kept)
        if tests:
            _write(tree, tests)
            g.red_at_base = not _ok(suite, tree, target, timeout)
            _git(tree, "checkout", "-q", "--", ".")
            _git(tree, "clean", "-qfd")
        _write(tree, kept)
        g.suite_green = _ok(suite, tree, target, timeout)
        _write(tree, held_back(case, clone))
        g.fixed = _ok(list(case.test), tree, target, timeout)
    return g


def held_back(case: FixCase, clone: Path) -> dict[str, str]:
    """The held-back test files: from the case dir if seeded, else from the fix commit."""
    if case.seeded:
        return dict(case.hidden)
    return {p: _git(clone, "show", f"{case.fix}:{p}") for p in case.held_back}


def check_case(case: FixCase, clone: Path, work: Path, timeout: int) -> tuple[bool, bool]:
    """Is the case valid? Held-back tests fail at the base and pass at the fix."""
    tree = work / f"check-{case.id.split('/')[1]}"
    target = _target(work, case)
    with _worktree(clone, case.base, tree):
        _write(tree, held_back(case, clone))
        red = not _ok(list(case.test), tree, target, timeout)
        _git(tree, "checkout", "-q", case.fix, "--", ".")
        green = _ok(list(case.test), tree, target, timeout)
    return red, green


def _target(work: Path, case: FixCase) -> Path:
    target = work / f"target-{case.id.split('/')[1]}"
    target.mkdir(parents=True, exist_ok=True)
    return target


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


def _ok(argv: list[str], tree: Path, target: Path, timeout: int) -> bool:
    """Fetch dependencies on the host (no code runs), then run `argv` in the sandbox."""
    bwrap = sandbox.bwrap()
    cargo_home, rustup_home = sandbox.toolchain()
    host = {**os.environ, "CARGO_TARGET_DIR": str(target)}
    try:
        subprocess.run(["cargo", "fetch"], cwd=tree, env=host, capture_output=True,
                       timeout=timeout, check=False)
        wrapped = sandbox.wrap(argv, tree, target, cargo_home, rustup_home, bwrap)
        return subprocess.run(wrapped, cwd=tree, capture_output=True, timeout=timeout,
                              check=False).returncode == 0
    except subprocess.TimeoutExpired:
        return False


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout
