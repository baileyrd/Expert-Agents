"""Split a promptfoo results file into one report per run, ready for grading."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunOutput:
    case: str
    variant: str
    prompt_sha: str
    run: int
    report: str
    latency_ms: int
    cost_usd: float | None
    tokens: int | None
    error: str | None  # set when the provider call failed; report is then empty


def run_outputs(results: dict[str, Any]) -> list[RunOutput]:
    """One RunOutput per result, numbered per case in the order promptfoo wrote them."""
    counts: dict[str, int] = {}
    outputs = []
    for r in results["results"]["results"]:
        v = r["vars"]
        counts[v["case"]] = counts.get(v["case"], 0) + 1
        response = r.get("response") or {}
        error = r.get("error") or (None if "response" in r else "no response")
        outputs.append(RunOutput(
            case=v["case"],
            variant=r["provider"].get("label") or r["provider"]["id"],
            prompt_sha=v["prompt_sha"],
            run=counts[v["case"]],
            report=str(response.get("output") or ""),
            latency_ms=int(r.get("latencyMs") or 0),
            cost_usd=r.get("cost"),
            tokens=(r.get("tokenUsage") or {}).get("total"),
            error=str(error) if error else None,
        ))
    return outputs


def report_md(o: RunOutput) -> str:
    """The reviewer's report with a header naming the run and its cost, or why it failed."""
    cost = f"${o.cost_usd:.2f}" if o.cost_usd is not None else "unknown"
    return (f"<!-- {o.case} run {o.run}, variant {o.variant}, prompt {o.prompt_sha}; "
            f"{o.latency_ms / 1000:.0f} s, {o.tokens or 'unknown'} tokens, {cost} -->\n\n"
            + (f"**Run failed:** {o.error}\n" if o.error else f"{o.report}\n"))


def stem(o: RunOutput) -> str:
    """`<case>-<n>`, the file stem for a run's report and graded-run file."""
    return f"{o.case.split('/')[1]}-{o.run}"


def run_skeleton(o: RunOutput) -> str:
    """A graded-run file for the grader to complete with one [[finding]] per finding."""
    return (f'# Grade {stem(o)}-report.md into [[finding]] tables; '
            "see bench/README.md.\n"
            f'case = "{o.case}"\nvariant = "{o.variant}"\nprompt_sha = "{o.prompt_sha}"\n'
            f"run = {o.run}\n")
