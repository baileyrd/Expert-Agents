"""Grounding: the commits that answered earlier reviews in a domain, minus the held-out PRs.

`parse_log` and `grounding_md` are pure; `review_fixes` and `held_out` run git.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bench.scorer.model import Case

_SEP, _FIELD = "\x1e", "\x1f"
# A subject that answers a review, or names a finding it fixes.
REVIEW = re.compile(r"\breview\b|\bfinding|\banswer\b|\bP[0-2]\b|\bfix(es|ed)?\b", re.I)


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str
    files: tuple[str, ...]


def parse_log(text: str) -> list[Commit]:
    """Parse `git log --format=%x1e%H%x1f%s%x1f%b%x1f --name-only`."""
    commits = []
    for record in text.split(_SEP)[1:]:
        sha, subject, body, rest = record.split(_FIELD, 3)
        files = tuple(line for line in rest.splitlines() if line.strip())
        commits.append(Commit(sha, subject.strip(), body.strip(), files))
    return commits


def answers_review(commit: Commit) -> bool:
    return bool(REVIEW.search(commit.subject) or REVIEW.search(commit.body))


def grounding_md(commits: list[Commit], held: frozenset[str], paths: tuple[str, ...]) -> str:
    """A table of review fixes for the generator to read, oldest last, held-out PRs removed."""
    kept = [c for c in commits if answers_review(c) and c.sha not in held]
    lines = [
        f"# Review fixes under {', '.join(paths)}", "",
        f"{len(kept)} commits answer a review; {len(held)} held-out commits removed.",
        "Read each one's diff (`git show <sha>`) for the defect class it fixed.", "",
        "| Commit | Subject | Files |", "|---|---|---|",
    ]
    for c in kept:
        shown = ", ".join(f"`{f}`" for f in c.files[:3]) + (" …" if len(c.files) > 3 else "")
        lines.append(f"| {c.sha[:10]} | {c.subject.replace('|', '/')} | {shown} |")
    return "\n".join(lines) + "\n"


def review_fixes(target: Path, paths: tuple[str, ...]) -> list[Commit]:
    """Every non-merge commit touching `paths`, newest first."""
    out = _git(target, "log", "--no-merges", f"--format={_SEP}%H{_FIELD}%s{_FIELD}%b{_FIELD}",
               "--name-only", "--", *paths)
    return parse_log(out)


def held_out(target: Path, cases: list[Case]) -> frozenset[str]:
    """Commits a case's answer key rests on: its PR up to the head and up to each fix."""
    ends = {(c.base, c.head) for c in cases} | {(c.base, k.fix) for c in cases for k in c.key}
    shas: set[str] = set()
    for base, end in ends:
        shas.update(_git(target, "rev-list", f"{base}..{end}").split())
    return frozenset(shas)


def _git(target: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(target), *args], capture_output=True, text=True,
                            check=False)
    if result.returncode != 0:
        raise ValueError(f"git {args[0]} in {target} failed: {result.stderr.strip()}")
    return result.stdout
