"""Grounding: the commits that answered earlier reviews in a domain, minus the held-out PRs.

`parse_log`, `parse_renames`, `aliases`, `merge_logs`, `answers_review` and `grounding_md`
are pure; `review_fixes` and `held_out` run git.
"""

from __future__ import annotations

import posixpath
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache, partial
from pathlib import Path

from bench.scorer.model import Case

_SEP, _FIELD = "\x1e", "\x1f"
# A message that answers a review or names a finding it fixes.
REVIEW = re.compile(r"\breview\b|\bfinding|\banswer\b|\bP[0-2]\b|\bfix(es|ed)?\b", re.I)
# A subject that names the defect it repairs ("events were silently dropped") without
# saying "fix". Subject only: bodies use these words in passing too often.
DEFECT = re.compile(
    r"\bbugs?\b|\bregress(ed|es)\b|\bsilent(ly)?\b|\bdropp(ed|ing)\b|\blost\b|\blos(es|ing)\b"
    r"|\bleak(s|ed|ing)?\b|\brac(e|es|y)\b|\bdeadlock|\bpanic(s|ked|king)?\b"
    r"|\bcrash(es|ed|ing)?\b|\b(over|under)flow|\bcorrupt|\boff-by-one\b|\bwrong\b"
    r"|\bincorrect", re.I)
# A subject for mechanical upkeep: formatting, lints, typos, or a conventional-commit
# type that never carries a defect. It is dropped even when it says "fix" or "P0".
NOISE = re.compile(
    r"\b(cargo |rust)?fmt\b|\bclippy\b|\blint(s|ing)?\b|\btypos?\b|\bwhitespace\b"
    r"|^(style|chore|docs?|ci|build|plan)(\([^)]*\))?!?:", re.I)


@dataclass(frozen=True)
class Commit:
    sha: str
    subject: str
    body: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class Rename:
    """`old` became `new` in commit `sha`."""
    sha: str
    old: str
    new: str


def parse_log(text: str) -> list[Commit]:
    """Parse `git log --format=%x1e%H%x1f%s%x1f%b%x1f --name-only`."""
    commits = []
    for record in text.split(_SEP)[1:]:
        sha, subject, body, rest = record.split(_FIELD, 3)
        files = tuple(line for line in rest.splitlines() if line.strip())
        commits.append(Commit(sha, subject.strip(), body.strip(), files))
    return commits


def parse_renames(text: str) -> list[Rename]:
    """Renames from `git log -z -M --diff-filter=R --name-status --format='commit %H'`.

    NUL-delimited, so paths come raw rather than quoted (non-ASCII, tabs, quotes).
    """
    tokens = text.split("\0")
    renames: list[Rename] = []
    sha, i = "", 0
    while i < len(tokens):
        token = tokens[i].lstrip("\n")  # git's record separator; path tokens stay raw
        if token.startswith("commit "):
            sha, i = token.removeprefix("commit "), i + 1
        elif token[:1] in ("R", "C") and i + 2 < len(tokens):
            if token[0] == "R":
                renames.append(Rename(sha, tokens[i + 1], tokens[i + 2]))
            i += 3
        else:
            i += 2 if token else 1
    return renames


def aliases(paths: tuple[str, ...], renames: list[Rename],
            is_ancestor: Callable[[str, str], bool]) -> tuple[Rename, ...]:
    """Every rename into `paths`, through any number of moves.

    An old name stands for this lineage only before the rename that ended it, so only
    renames that are ancestors of that commit count, and its history is read only up to
    it (see `review_fixes`). `is_ancestor(a, b)` says whether commit a precedes b.
    """
    found: list[Rename] = []
    # (scope, the rename commit that ended it, or None for a path as it is today)
    scopes: list[tuple[str, str | None]] = [(posixpath.normpath(p), None) for p in paths]
    grew = True
    while grew:
        grew = False
        for r in renames:
            if r not in found and any(_under(r.new, s) and _before(r.sha, end, is_ancestor)
                                      for s, end in scopes):
                found.append(r)
                scopes.append((r.old, r.sha))
                grew = True
    return tuple(found)


def _before(sha: str, end: str | None, is_ancestor: Callable[[str, str], bool]) -> bool:
    return end is None or (sha != end and is_ancestor(sha, end))


def merge_logs(logs: list[list[Commit]], order: list[str]) -> list[Commit]:
    """One commit per sha across `logs`, files unioned, in `order` (newest first)."""
    merged: dict[str, Commit] = {}
    for log in logs:
        for c in log:
            seen = merged.get(c.sha)
            files = c.files if seen is None else seen.files + tuple(
                f for f in c.files if f not in seen.files)
            merged[c.sha] = Commit(c.sha, c.subject, c.body, files)
    return [merged[sha] for sha in order if sha in merged]


def answers_review(commit: Commit) -> bool:
    """A commit that repairs a defect, not mechanical upkeep that happens to say "fix"."""
    if NOISE.search(commit.subject):
        return False
    return bool(REVIEW.search(commit.subject) or REVIEW.search(commit.body)
                or DEFECT.search(commit.subject))


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
    """Every non-merge commit touching `paths`, or their names before a rename, newest first."""
    renames = parse_renames(_git(target, "log", "-z", "-M", "--diff-filter=R",
                                 "--name-status", "--format=commit %H"))
    found = aliases(paths, renames, lru_cache(maxsize=None)(partial(_is_ancestor, target)))
    if not found:
        return _log(target, (), paths)
    by_rename: dict[str, list[str]] = {}
    for r in found:
        by_rename.setdefault(r.sha, []).append(r.old)
    logs = [_log(target, (), paths)]
    logs += [_log(target, (f"{sha}^",), tuple(olds)) for sha, olds in by_rename.items()]
    shas = {c.sha for log in logs for c in log}
    order = _git(target, "rev-list", "--no-walk=sorted", *shas).split()
    return merge_logs(logs, order)


def _is_ancestor(target: Path, sha: str, of: str) -> bool:
    result = subprocess.run(["git", "-C", str(target), "merge-base", "--is-ancestor", sha, of],
                            capture_output=True, text=True, check=False)
    if result.returncode not in (0, 1):
        raise ValueError(f"git merge-base in {target} failed: {result.stderr.strip()}")
    return result.returncode == 0


def _log(target: Path, revs: tuple[str, ...], paths: tuple[str, ...]) -> list[Commit]:
    return parse_log(_git(target, "--literal-pathspecs", "log", "--no-merges",
                          f"--format={_SEP}%H{_FIELD}%s{_FIELD}%b{_FIELD}", "--name-only",
                          *revs, "--", *paths))


def held_out(target: Path, cases: list[Case]) -> frozenset[str]:
    """Commits a case's answer key rests on: its PR up to the head and up to each fix."""
    ends = {(c.base, c.head) for c in cases} | {(c.base, k.fix) for c in cases for k in c.key}
    shas: set[str] = set()
    for base, end in ends:
        shas.update(_git(target, "rev-list", f"{base}..{end}").split())
    return frozenset(shas)


def _under(path: str, scope: str) -> bool:
    return scope == "." or path == scope or path.startswith(scope + "/")


def _git(target: Path, *args: str) -> str:
    """git's stdout, decoded as-is: no newline translation, so raw paths keep a CR, and
    undecodable bytes round-trip back into git arguments via surrogateescape."""
    result = subprocess.run(["git", "-C", str(target), *args], capture_output=True, check=False)
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", "replace").strip()
        raise ValueError(f"git {' '.join(args[:2])} in {target} failed: {err}")
    return result.stdout.decode("utf-8", "surrogateescape")
