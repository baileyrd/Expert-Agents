"""Prompt sections: split a prompt by its "## " headings, and drop some for ablations."""

from __future__ import annotations

import re

_FRONTMATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_HEADING = re.compile(r"^## (.+?)\s*$", re.M)


def body(prompt: str) -> str:
    """The prompt without its YAML frontmatter (a Claude subagent's name and tools)."""
    return _FRONTMATTER.sub("", prompt, count=1)


def sections(prompt: str) -> dict[str, str]:
    """Each section's text by heading, in order; "" is the text before the first heading."""
    text = body(prompt)
    starts = [(m.start(), m.group(1)) for m in _HEADING.finditer(text)]
    out: dict[str, str] = {}
    preamble = text[: starts[0][0]] if starts else text
    if preamble.strip():
        out[""] = preamble
    for i, (start, heading) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(text)
        out[heading] = text[start:end]
    return out


def without(prompt: str, drop: frozenset[str]) -> str:
    """The prompt body with the named sections removed."""
    parts = sections(prompt)
    missing = drop - parts.keys()
    if missing:
        raise ValueError(f"no such sections: {sorted(missing)}")
    return "".join(text for heading, text in parts.items() if heading not in drop)
