"""promptfoo configs for a case: the blind review, and the rubric grading after it. Pure.

The two are separate on purpose. The review config holds no answer key, so nothing the
reviewer can reach while it runs says what it should find; the rubric config is written
only after the reviews are done, from their saved reports.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bench.scorer.model import Case, KeyFinding

PROVIDER = "anthropic:claude-agent-sdk"
TOOLS = ("Read", "Grep", "Glob", "Bash")
# Always off: the sandbox's domain allowlist covers Bash only, not these, and any web
# access reaches the live review on github.com.
WEB_TOOLS = ("WebFetch", "WebSearch")
TASK = (
    "Review PR {{pr}} of {{repo}} against base {{base}} at head {{head}}. "
    # No "/" here: promptfoo reads a prompt that looks like a path as a prompt file.
    "The working directory is a checkout of the head with history up to it, and its origin "
    "serves only the PR head ref."
)
RUBRIC = """\
Grade whether this code review reports the following known defect as one of its findings.

Defect: {title}
{summary}

It counts as caught if the review {caught_if} Score 1 if caught, 0.5 if the review
reports it only in part (for example one of several affected paths), 0 if missed.
Wording does not matter; the defect does."""


@dataclass(frozen=True)
class Boundary:
    """What the reviewer may touch.

    `hidden` are paths it must not read: this repo, with every answer key; the target clone,
    with the fix commits; and the work directory's grade dir, where results and rubrics go.
    `domains` is Bash's network allowlist inside the sandbox; empty means no network. The
    web tools stay off whatever it holds.
    """

    checkout: Path
    scratch: Path
    hidden: tuple[Path, ...]
    domains: tuple[str, ...] = ()


def review(case: Case, prior: Case | None, system_prompt: str, variant: str, prompt_sha: str,
           runs: tuple[Boundary, ...], model: str | None) -> dict[str, Any]:
    """The reviewer's config: the case's task and prompt in a sandbox, with no answer key.

    One provider per run, each with its own checkout and scratch, so concurrent or later
    runs never see each other's builds, probes or notes.
    """
    return {
        "description": f"{case.id} at {case.head[:8]}, variant {variant}",
        "prompts": [{"raw": TASK + _prior(prior), "label": "review"}],
        "providers": [{"id": PROVIDER, "label": f"{variant} run {n}",
                       "config": _reviewer(system_prompt, b, model)}
                      for n, b in enumerate(runs, start=1)],
        "tests": [{"description": case.id, "vars": {
            "case": case.id, "variant": variant, "prompt_sha": prompt_sha, "repo": case.repo,
            "pr": case.pr, "base": case.base, "head": case.head}}],
    }


def _reviewer(system_prompt: str, boundary: Boundary, model: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "working_dir": str(boundary.checkout),
        "custom_system_prompt": system_prompt,
        "custom_allowed_tools": list(TOOLS),
        "disallowed_tools": _disallowed(boundary),
        "sandbox": _sandbox(boundary),
        "persist_session": False,
        "apiKeyRequired": False,  # use the signed-in Claude Code login
    }
    if model:
        config["model"] = model
    return config


def rubric(case: Case, reports: list[tuple[str, str]], grader: str) -> dict[str, Any]:
    """Grade saved reports, as (label, text) pairs, for recall: one llm-rubric per key finding."""
    return {
        "description": f"{case.id} rubric recall",
        "prompts": [{"raw": "{{report}}", "label": "report"}],
        "providers": [{"id": "echo"}],  # returns the report itself for the rubrics to grade
        "defaultTest": {"options": {"provider": _grader(grader)}},
        "tests": [{"description": label, "vars": {"report": text},
                   "assert": [_rubric(k) for k in case.key]} for label, text in reports],
    }


def _disallowed(boundary: Boundary) -> list[str]:
    rules = ["Edit", "Write", "NotebookEdit", *WEB_TOOLS]
    rules += [f"Read(/{p}/**)" for p in boundary.hidden]  # "//abs" is an absolute path rule
    return rules


def _sandbox(boundary: Boundary) -> dict[str, Any]:
    """Bash runs sandboxed or not at all; a missing sandbox fails the run."""
    return {
        "enabled": True,
        "failIfUnavailable": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {
            "denyRead": [str(p) for p in boundary.hidden],
            "allowWrite": [str(boundary.checkout), str(boundary.scratch)],
        },
        "network": {"allowedDomains": list(boundary.domains)},
    }


def _grader(grader: str) -> dict[str, Any]:
    """Anthropic graders also use the Claude Code login instead of an API key."""
    config = {"apiKeyRequired": False} if grader.startswith("anthropic:") else {}
    return {"id": grader, "config": config}


def _prior(prior: Case | None) -> str:
    if prior is None:
        return ""
    listed = "\n".join(f"- {k.id} ({k.severity.name if k.severity else 'blocking'}): {k.title}"
                       for k in prior.key)
    return f"\n\nPrior findings, from the review of {prior.head[:8]}; re-check each at this head:\n{listed}"


def _rubric(key: KeyFinding) -> dict[str, Any]:
    caught_if = key.caught_if[0].lower() + key.caught_if[1:]
    return {
        "type": "llm-rubric",
        "metric": key.id,
        "threshold": 0.5,
        "value": RUBRIC.format(title=key.title, summary=key.summary, caught_if=caught_if),
    }
