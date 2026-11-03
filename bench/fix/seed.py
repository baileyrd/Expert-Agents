"""Plant a seeded case's bug as a parentless commit in the target clone. Runs git.

The commit is built from the clean `fix` tree plus the case's `seed` patch, through a
private index (no worktree), with a fixed author, date and message. Everything that goes
into it is fixed, so every clone builds the same SHA, and the case pins that SHA as its
`base`. It has no parent, so a checkout of it carries no history that shows the diff.
A ref under `refs/bench/seed/` keeps it from being garbage-collected.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from bench.fix.model import FixCase

IDENTITY = {
    "GIT_AUTHOR_NAME": "rusty_mill", "GIT_AUTHOR_EMAIL": "snapshot@localhost",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+00:00",
    "GIT_COMMITTER_NAME": "rusty_mill", "GIT_COMMITTER_EMAIL": "snapshot@localhost",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+00:00",
}
MESSAGE = "rusty_mill snapshot"


def ref(case: FixCase) -> str:
    return f"refs/bench/seed/{case.id}"


def plant(case: FixCase, clone: Path) -> str:
    """Create (or recreate) the seeded commit and its ref; return its SHA."""
    if not case.seeded:
        raise ValueError(f"{case.id}: not a seeded case")
    with tempfile.TemporaryDirectory() as tmp:
        patch = Path(tmp) / "seed.patch"
        patch.write_text(case.seed, encoding="utf-8")
        env = {**os.environ, **IDENTITY, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        _git(clone, env, "read-tree", case.fix)
        _git(clone, env, "apply", "--cached", str(patch))
        tree = _git(clone, env, "write-tree")
        sha = _git(clone, env, "commit-tree", "--no-gpg-sign", "-m", MESSAGE, tree)
    _git(clone, os.environ.copy(), "update-ref", ref(case), sha)
    return sha


def _git(cwd: Path, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True,
                            check=False)
    if result.returncode != 0:
        raise ValueError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()
