"""Checks a generated expert before it is benched. Pure: takes texts and cases, returns problems.

A problem blocks the bench run. The worst one is a leak: a prompt that names a PR or commit
from its own answer keys measures memory, not review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bench.prompt import sections
from bench.scorer.model import Case

from .template import PLACEHOLDERS, TODO

_REQUIRED = ("Authority", "Pin the review", "Return")
_SHA_PREFIX = 7
MEASURED_PRS = 3  # an expert is measured on three or more past PRs


@dataclass(frozen=True)
class Expert:
    """An expert as read from disk: its manifest and each prompt's text by kind."""

    dir_name: str
    manifest: dict
    subagent: str
    codex: str


def problems(expert: Expert, cases: list[Case]) -> list[str]:
    """Everything that keeps this expert off the bench, most fundamental first."""
    return [*_manifest(expert), *_frontmatter(expert), *_sections(expert), *_codex(expert),
            *_todos(expert), *_leaks(expert, cases)]


def notes(cases: list[Case]) -> list[str]:
    """What does not block a run but keeps the expert from counting as measured."""
    prs = {(c.repo, c.pr) for c in cases}
    if len(prs) >= MEASURED_PRS:
        return []
    return [f"{len(prs)} past PR(s) in bench/cases; measured needs {MEASURED_PRS}"]


def _manifest(e: Expert) -> list[str]:
    out = []
    if e.manifest.get("name") != e.dir_name:
        out.append(f"expert.toml name {e.manifest.get('name')!r} is not the folder {e.dir_name!r}")
    kinds = sorted(p.get("kind", "") for p in e.manifest.get("prompt", []))
    if kinds != ["claude-subagent", "codex"]:
        out.append(f"expert.toml declares prompts {kinds}; want one claude-subagent and one codex")
    return out


def _frontmatter(e: Expert) -> list[str]:
    m = re.match(r"\A---\nname: ([\w-]+)\ndescription: (.+)\ntools: .+\n---\n", e.subagent)
    if m is None:
        return ["prompt.md frontmatter is not name, description, tools"]
    want = f"{e.dir_name}-expert"
    return [] if m.group(1) == want else [f"prompt.md name {m.group(1)!r}; want {want!r}"]


def _sections(e: Expert) -> list[str]:
    declared = {s["heading"] for s in e.manifest.get("section", [])}
    out = [f"expert.toml does not declare required section {h!r}"
           for h in _REQUIRED if h not in declared]
    for label, text in (("prompt.md", e.subagent), ("codex.md", e.codex)):
        found = set(sections(text))
        if found != declared:
            out.append(f"{label} headings differ from expert.toml: "
                       f"missing {sorted(declared - found)}, extra {sorted(found - declared)}")
    return out


def _codex(e: Expert) -> list[str]:
    return [f"codex.md lacks placeholder {p}" for p in PLACEHOLDERS if p not in e.codex]


def _todos(e: Expert) -> list[str]:
    texts = (("prompt.md", e.subagent), ("codex.md", e.codex))
    return [f"{label} still has {TODO} markers" for label, text in texts if TODO in text]


def _leaks(e: Expert, cases: list[Case]) -> list[str]:
    """A held-out PR's number or any of its SHAs, in either prompt."""
    out = []
    for c in cases:
        pr = re.compile(rf"(#|\bPR\s*|/pull/){c.pr}\b", re.I)
        shas = {c.base, c.head, *(k.fix for k in c.key)}
        for label, text in (("prompt.md", e.subagent), ("codex.md", e.codex)):
            if pr.search(text):
                out.append(f"{label} names held-out PR {c.pr} ({c.id})")
            out += [f"{label} names held-out commit {s[:10]} ({c.id})"
                    for s in sorted(shas) if s[:_SHA_PREFIX] in text]
    return sorted(set(out))
