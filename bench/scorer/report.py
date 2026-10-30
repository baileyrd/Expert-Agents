"""Markdown for a summary of N runs on one case."""

from __future__ import annotations

from .model import Case
from .score import Summary


def render(case: Case, summary: Summary) -> str:
    """One case's results as a Markdown section for `reports/`."""
    lines = [
        f"## {case.id}: PR #{case.pr} round {case.round} at `{case.head[:8]}`",
        "",
        f"Variant `{summary.variant}`, {len(summary.runs)} run(s).",
        "",
        f"- Recall: {_spread(summary.recall)}",
        f"- Precision: {_spread(summary.precision) if summary.precision else 'n/a'}",
        f"- Severity vs key: {_severity(summary)}",
        f"- Findings by class: {_triage(summary)}",
        "",
        "| Key | Severity | Finding | Hit rate |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| {k.id} | {k.severity.name if k.severity else 'ungraded'} | {k.title} | {summary.hit_rate(k.id):.2f} |"
        for k in case.key
    ]
    return "\n".join(lines) + "\n"


def _spread(value: tuple[float, float, float]) -> str:
    avg, low, high = value
    return f"{avg:.2f}" if low == high else f"{avg:.2f} (min {low:.2f}, max {high:.2f})"


def _severity(summary: Summary) -> str:
    s = summary.severity
    return f"{s.agree} agree, {s.over} over, {s.under} under"


def _triage(summary: Summary) -> str:
    counts = summary.triage
    return ", ".join(f"{name} {counts[name]}" for name in sorted(counts)) or "none"
