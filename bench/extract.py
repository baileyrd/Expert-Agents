"""Turn an Eval-Kit store into one report per run, ready for grading. Pure.

The store (`.evalkit/suite.jsonl` beside a suite) is append-only: a line per run and a
line per judge verdict. When a run was repeated, its last line wins, as in Eval-Kit.
Eval-Kit stores only successful runs; one that failed is retried by `evalkit run`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunOutput:
    case: str  # "<expert>/<case dir>"
    variant: str
    prompt_sha: str
    model: str
    run: int  # 1-based, from Eval-Kit's 0-based sample
    report: str
    latency_ms: int
    cost_usd: float | None
    tokens: int | None
    cli_version: str


def run_outputs(store_lines: list[str], expert: str, variant: str, model: str,
                prompt_sha: str) -> list[RunOutput]:
    """The variant's runs, latest per case and sample, ordered by case then run."""
    latest: dict[tuple[str, int], dict[str, Any]] = {}
    for line in store_lines:
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("kind") == "verdict" or record["variant"] != variant:
            continue
        latest[(record["case"], int(record["sample"]))] = record
    return [_output(r, expert, variant, model, prompt_sha) for _, r in sorted(latest.items())]


def _output(record: dict[str, Any], expert: str, variant: str, model: str,
            prompt_sha: str) -> RunOutput:
    trace = record["trace"]
    return RunOutput(
        case=f"{expert}/{record['case']}",
        variant=variant,
        prompt_sha=prompt_sha,
        model=model,
        run=int(record["sample"]) + 1,
        report=str(trace["final_text"]),
        latency_ms=int(trace.get("duration_ms") or 0),
        cost_usd=trace.get("cost_usd"),
        tokens=int(trace.get("input_tokens") or 0) + int(trace.get("output_tokens") or 0) or None,
        cli_version=str(record["cli_version"]),
    )


def report_md(o: RunOutput) -> str:
    """The reviewer's report with a header naming the run and its cost."""
    cost = f"${o.cost_usd:.2f}" if o.cost_usd is not None else "unknown"
    return (f"<!-- {o.case} run {o.run}, variant {o.variant}, prompt {o.prompt_sha}, "
            f"model {o.model}, {o.cli_version}; "
            f"{o.latency_ms / 1000:.0f} s, {o.tokens or 'unknown'} tokens, {cost} -->\n\n"
            f"{o.report}\n")


def stem(o: RunOutput) -> str:
    """`<case>-<n>`, the file stem for a run's report and graded-run file."""
    return f"{o.case.split('/')[1]}-{o.run}"


def run_skeleton(o: RunOutput) -> str:
    """A graded-run file for the grader to complete with one [[finding]] per finding."""
    return (f'# Grade {stem(o)}-report.md into [[finding]] tables; '
            "see bench/README.md.\n"
            f'case = "{o.case}"\nvariant = "{o.variant}"\nprompt_sha = "{o.prompt_sha}"\n'
            f"run = {o.run}\n")
