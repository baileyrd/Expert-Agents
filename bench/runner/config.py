"""The promptfoo config for one case and one prompt variant. Pure: no I/O."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from bench.scorer.model import Case, KeyFinding

PROVIDER = "anthropic:claude-agent-sdk"
# The reviewer's tools: read the tree, run git and cargo, consult reference sources.
TOOLS = ("Read", "Grep", "Glob", "Bash", "WebFetch", "WebSearch")
TASK = (
    "Review PR {{pr}} of {{repo}} against base {{base}} at head {{head}}. "
    "The working directory is a checkout of the head with history up to it; its origin serves only "
    "pull/{{pr}}/head."
)
RUBRIC = """\
Grade whether this code review reports the following known defect as one of its findings.

Defect: {title}
{summary}

It counts as caught if the review {caught_if} Score 1 if caught, 0.5 if the review
reports it only in part (for example one of several affected paths), 0 if missed.
Wording does not matter; the defect does."""


def build(case: Case, prior: Case | None, system_prompt: str, variant: str, prompt_sha: str,
          checkout: Path, grader: str, model: str | None) -> dict[str, Any]:
    """A promptfoo config that runs the reviewer on the case and grades recall per key finding.

    A re-review case passes its prior round's key as the findings to re-check.
    """
    provider: dict[str, Any] = {
        "id": PROVIDER,
        "label": variant,
        "config": {
            "working_dir": str(checkout),
            "custom_system_prompt": system_prompt,
            "custom_allowed_tools": list(TOOLS),
            "persist_session": False,
            "apiKeyRequired": False,  # use the signed-in Claude Code login
        },
    }
    if model:
        provider["config"]["model"] = model
    return {
        "description": f"{case.id} at {case.head[:8]}, variant {variant}",
        "prompts": [TASK + _prior(prior)],
        "providers": [provider],
        "defaultTest": {"options": {"provider": _grader(grader)}},
        "tests": [{
            "description": case.id,
            "vars": {"case": case.id, "prompt_sha": prompt_sha, "repo": case.repo, "pr": case.pr,
                     "base": case.base, "head": case.head},
            "assert": [_rubric(k) for k in case.key],
        }],
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
