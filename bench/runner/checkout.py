"""Blind checkout: a fresh repository holding only the case's head and its history."""

from __future__ import annotations

import subprocess
from pathlib import Path

from bench.scorer.model import Case

# A local fetch by SHA needs the source's upload-pack to allow unadvertised objects.
_UPLOAD_PACK = "git -c uploadpack.allowAnySHA1InWant=true upload-pack"


class CheckoutError(RuntimeError):
    """The target clone could not provide the case's commits."""


def blind_checkout(case: Case, target: Path, dest: Path) -> None:
    """Check out `case.head` at `dest` with no later commits, no other refs and no remote.

    The reviewer must not see the fix commits or the review, so this is a new repository
    fetched by SHA from a local clone of the target, not a worktree of it.
    """
    if dest.exists():
        raise CheckoutError(f"{dest} already exists; use a fresh work directory")
    _git(None, "init", "-q", str(dest))
    _git(dest, "fetch", "-q", "--no-tags", "--upload-pack", _UPLOAD_PACK, str(target.resolve()),
         case.base, case.head)
    _git(dest, "checkout", "-q", "--detach", case.head)


def _git(cwd: Path | None, *args: str) -> None:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise CheckoutError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()}")
