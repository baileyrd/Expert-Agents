"""Grounding: the commits that answered earlier reviews in a domain, minus the held-out PRs.

`parse_log`, `parse_renames`, `former_paths`, `answers_review` and `grounding_md` are pure;
`review_fixes` and `held_out` run git.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
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


def parse_log(text: str) -> list[Commit]:
    """Parse `git log --format=%x1e%H%x1f%s%x1f%b%x1f --name-only`."""
    commits = []
    for record in text.split(_SEP)[1:]:
        sha, subject, body, rest = record.split(_FIELD, 3)
        files = tuple(line for line in rest.splitlines() if line.strip())
        commits.append(Commit(sha, subject.strip(), body.strip(), files))
    return commits


def parse_renames(text: str) -> list[tuple[str, str]]:
    """(old, new) pairs from `git log -M --diff-filter=R --name-status --format=`."""
    pairs = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) == 3 and fields[0].startswith("R"):
            pairs.append((fields[1], fields[2]))
    return pairs


def former_paths(paths: tuple[str, ...], renames: list[tuple[str, str]]) -> tuple[str, ...]:
    """`paths` plus every file that was later renamed into them, through any number of moves."""
    found = list(paths)
    grew = True
    while grew:
        grew = False
        for old, new in renames:
            if old not in found and any(_under(new, p) for p in found):
                found.append(old)
                grew = True
    return tuple(found)


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
    """Every non-merge commit touching `paths` or their names before a rename, newest first."""
    renames = parse_renames(_git(target, "log", "-M", "--diff-filter=R", "--name-status",
                                 "--format="))
    out = _git(target, "log", "--no-merges", f"--format={_SEP}%H{_FIELD}%s{_FIELD}%b{_FIELD}",
               "--name-only", "--", *former_paths(paths, renames))
    return parse_log(out)


def held_out(target: Path, cases: list[Case]) -> frozenset[str]:
    """Commits a case's answer key rests on: its PR up to the head and up to each fix."""
    ends = {(c.base, c.head) for c in cases} | {(c.base, k.fix) for c in cases for k in c.key}
    shas: set[str] = set()
    for base, end in ends:
        shas.update(_git(target, "rev-list", f"{base}..{end}").split())
    return frozenset(shas)


def _under(path: str, scope: str) -> bool:
    scope = scope.rstrip("/")
    return scope in ("", ".") or path == scope or path.startswith(scope + "/")


def _git(target: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(target), *args], capture_output=True, text=True,
                            check=False)
    if result.returncode != 0:
        raise ValueError(f"git {args[0]} in {target} failed: {result.stderr.strip()}")
    return result.stdout
