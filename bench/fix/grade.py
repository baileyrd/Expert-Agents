"""Grade a developer's fix-bench runs by executing tests. Runs git and cargo.

For each stored run, each step in a fresh export of the case's base (no `.git`):

- **fixed**: the run's changed files applied, then the held-back test files from the
  real fix on top; the case's test command passes.
- **red_at_base**: only the run's own changed test files (under a `tests/` directory)
  applied to the base; the developer's suite command fails with them. `None` when the
  run added no such file (its tests may live in `#[cfg(test)]` modules, which cannot be
  separated from the fix).
- **suite_green**: with the run's changes alone, the developer's suite command passes.

Each step that does not pass records why in `failures`: "timeout after Ns", or the
failing tests and panics from the command's output, so a timeout or a flaky test in the
suite is told apart from a broken fix.

A run is **solved** when it is fixed and suite_green. Eval-Kit's trace keeps the text of
each file the run added or changed (not deletions, not files over 200 kB); a file it
could not keep makes the run unreconstructable and it is reported as such.

A run's files are model output, and its build scripts and tests are code, so:

- dependencies are fetched on the host only from the unmodified export, before any run
  file is written; cargo never runs on the host after that;
- run files are written only to plain relative paths, never through a symlink;
- every test command runs in the sandbox in `bench.fix.sandbox`, and each step gets a
  fresh tree and is deleted after, so nothing on the host reads or writes a tree the
  model's code has run in. Each step gets its own target dir.
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
TAIL_LINES = 20


@dataclass(frozen=True)
class Outcome:
    """How one sandboxed command ended: `detail` says why when it did not pass."""
    passed: bool
    detail: str = ""


@dataclass
class Grade:
    case: str
    run: int
    key: str = ""  # Eval-Kit's content key of the graded run (prompt, model, settings, case)
    fixed: bool | None = None
    red_at_base: bool | None = None
    suite_green: bool | None = None
    changed: list[str] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)  # step -> why it did not pass
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
              suite: list[str], timeout: int, suite_timeout: int) -> Grade:
    g = Grade(case.id, run, key)
    kept, lost = source_files(files)
    g.changed = sorted(kept)
    bad = [p for p in kept if not safe_path(p)]
    if lost or bad:
        g.note = f"unreconstructable: {', '.join(lost)}" if lost else f"rejected paths: {bad}"
        return g
    if not kept:
        g.note = "no source changes"
        g.fixed = g.suite_green = False
        return g
    name = work / f"{case.id.split('/')[1]}-{run}"
    tests = own_tests(kept)
    if tests:
        red = _phase(clone, case.base, f"{name}-red", tests, suite, suite_timeout)
        g.red_at_base = not red.passed
    suite_run = _phase(clone, case.base, f"{name}-suite", kept, suite, suite_timeout)
    g.suite_green = _record(g, "suite", suite_run)
    fixed = _phase(clone, case.base, f"{name}-fixed", {**kept, **held_back(case, clone)},
                   list(case.test), timeout)
    g.fixed = _record(g, "fixed", fixed)
    return g


def _record(g: Grade, step: str, outcome: Outcome) -> bool:
    if not outcome.passed:
        g.failures[step] = outcome.detail
    return outcome.passed


def held_back(case: FixCase, clone: Path) -> dict[str, str]:
    """The held-back test files: from the case dir if seeded, else from the fix commit."""
    if case.seeded:
        return dict(case.hidden)
    return {p: _git(clone, "show", f"{case.fix}:{p}") for p in case.held_back}


def check_case(case: FixCase, clone: Path, work: Path, timeout: int) -> tuple[bool, bool]:
    """Is the case valid? Held-back tests fail at the base and pass at the fix."""
    name = work / f"check-{case.id.split('/')[1]}"
    tests = held_back(case, clone)
    red = not _phase(clone, case.base, f"{name}-base", tests, list(case.test), timeout).passed
    green = _phase(clone, case.fix, f"{name}-fix", tests, list(case.test), timeout).passed
    return red, green


def check_suite(case: FixCase, clone: Path, work: Path, suite: list[str],
                timeout: int) -> Outcome:
    """Does the developer's suite pass at the case base, unmodified? If not, every run's
    suite_green is false and red_at_base true whatever the run did."""
    name = work / f"check-{case.id.split('/')[1]}-suite"
    return _phase(clone, case.base, str(name), {}, suite, timeout)


def safe_path(rel: str) -> bool:
    """A relative path inside the tree: no absolute path, `..`, empty part or `.git`."""
    parts = rel.split("/")
    return bool(rel) and not rel.startswith("/") and "\\" not in rel and all(
        p not in ("", ".", "..", ".git") for p in parts)


def _phase(clone: Path, sha: str, name: str, files: dict[str, str], argv: list[str],
           timeout: int) -> Outcome:
    """One grading step in a fresh tree and target dir: export `sha`, fetch its
    dependencies on the host (trusted inputs only, before any model file lands), write
    `files`, run `argv` in the sandbox, delete both. Nothing on the host touches the tree
    after the sandbox ran, and no build output carries over between steps or runs."""
    tree, target = Path(name), Path(f"{name}-target")
    _export(clone, sha, tree)
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    try:
        _fetch(tree, target, timeout)
        _write(tree, files)
        return _sandboxed(argv, tree, target, timeout)
    finally:
        shutil.rmtree(tree, ignore_errors=True)
        shutil.rmtree(target, ignore_errors=True)


def _export(clone: Path, sha: str, tree: Path) -> None:
    """A plain copy of `sha` (no `.git`), so no host git ever runs in a graded tree."""
    shutil.rmtree(tree, ignore_errors=True)
    tree.mkdir(parents=True)
    archive = subprocess.Popen(["git", "archive", sha], cwd=clone, stdout=subprocess.PIPE)
    untar = subprocess.run(["tar", "-x", "-m", "-C", str(tree)], stdin=archive.stdout,
                           capture_output=True, check=False)
    if archive.stdout:
        archive.stdout.close()
    if archive.wait() != 0 or untar.returncode != 0:
        raise ValueError(f"exporting {sha[:12]} failed: {untar.stderr.decode().strip()}")


def _write(tree: Path, files: dict[str, str]) -> None:
    """Write `files` under `tree`, refusing unsafe paths and any symlink on the way."""
    root = tree.resolve()
    for rel, text in files.items():
        if not safe_path(rel):
            raise ValueError(f"refusing to write {rel!r}: not a plain relative path")
        here = root
        for part in rel.split("/")[:-1]:
            here = here / part
            if here.is_symlink():
                raise ValueError(f"refusing to write {rel!r}: {here} is a symlink")
            here.mkdir(exist_ok=True)
        fd = os.open(root / rel, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)


def _fetch(tree: Path, target: Path, timeout: int) -> None:
    """Download the dependencies of an unmodified export: trusted manifests, no model file."""
    subprocess.run(["cargo", "fetch"], cwd=tree,
                   env={**os.environ, "CARGO_TARGET_DIR": str(target)},
                   capture_output=True, timeout=timeout, check=False)


def _sandboxed(argv: list[str], tree: Path, target: Path, timeout: int) -> Outcome:
    bwrap = sandbox.bwrap()
    cargo_home, rustup_home = sandbox.toolchain()
    wrapped = sandbox.wrap(argv, tree, target, cargo_home, rustup_home, bwrap)
    try:
        done = subprocess.run(wrapped, cwd=tree, capture_output=True, text=True,
                              errors="replace", timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return Outcome(False, f"timeout after {timeout}s")
    if done.returncode == 0:
        return Outcome(True)
    return Outcome(False, summarize(done.stdout + done.stderr))


def summarize(output: str) -> str:
    """Why a cargo command failed: its failed tests, panics and errors, else its tail."""
    lines = output.splitlines()
    marks = ("FAILED", "panicked at", "error:", "error[", "failures:")
    hits = [line.strip() for line in lines if any(m in line for m in marks)]
    return "\n".join((hits or lines)[-TAIL_LINES:])


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout
