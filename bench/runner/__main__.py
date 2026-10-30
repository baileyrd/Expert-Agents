"""Bench runner.

  python -m bench.runner prepare <case-dir> --target <clone> --out <dir>
      Check the case out blind and write the review's promptfoo config.
  python -m bench.runner extract <results.json> --out <reports dir>
      Write each run's report and a graded-run skeleton, then the rubric config.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

from bench.prompt import without
from bench.scorer.load import load_case
from bench.scorer.model import BenchError

from .checkout import CheckoutError, blind_checkout
from .config import Boundary, review, rubric
from .extract import RunOutput, report_md, run_outputs, run_skeleton, stem

ROOT = Path(__file__).resolve().parent.parent.parent
EXPERTS = ROOT / "experts"
CASES = ROOT / "bench" / "cases"
GRADER = "anthropic:messages:claude-sonnet-5-5"
EVAL = "npx --prefix bench/runner promptfoo eval --no-cache"


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.command(args)
    except (BenchError, CheckoutError, ValueError, KeyError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


def prepare(args: argparse.Namespace) -> int:
    """Check out the case blind and write its review config, then print the eval command."""
    case = load_case(args.case)
    prior = load_case(args.case.parent / case.prior) if case.prior else None
    prompt = without(_subagent_prompt(case.expert), _droppable(case.expert, args.drop))
    out, target = args.out.resolve(), args.target.resolve()
    _outside(out, (ROOT, target))
    name = case.id.replace("/", "-")
    boundary = Boundary(checkout=out / "review" / name, scratch=out / "scratch",
                        hidden=(ROOT, target), domains=tuple(args.allow_domain))
    blind_checkout(case, target, boundary.checkout)
    boundary.scratch.mkdir(parents=True, exist_ok=True)
    config = review(case, prior, prompt, args.variant, _prompt_sha(), boundary, args.model)
    path = out / f"{name}.review.yaml"
    path.write_text(json.dumps(config, indent=2) + "\n")  # JSON is valid YAML
    print(f"{EVAL} -c {path} --repeat {args.repeat} -o {out / 'results.json'}")
    return 0


def extract(args: argparse.Namespace) -> int:
    """Write `<case>-<n>-report.md` per run and `<case>-<n>.toml` for each that succeeded.

    Refuses before writing anything if a file it would write exists, so a re-run never
    replaces a graded run. A failed run keeps a report naming the error and gets no
    skeleton, so it is never scored as a review with no findings; it makes the exit
    status 1. The rubric config is written beside the results, for the successful runs.
    """
    results: Path = args.results
    outputs = run_outputs(json.loads(results.read_text()))
    _refuse_collisions(args.out, outputs)
    args.out.mkdir(parents=True, exist_ok=True)
    failed = [o for o in outputs if o.error]
    for o in outputs:
        (args.out / f"{stem(o)}-report.md").write_text(report_md(o))
        if not o.error:
            (args.out / f"{stem(o)}.toml").write_text(run_skeleton(o))
    for o in failed:
        print(f"FAILED {args.out / stem(o)}-report.md: {o.error}", file=sys.stderr)
    _write_rubrics(results.parent, [o for o in outputs if not o.error], args.grader)
    return 1 if failed else 0


def _write_rubrics(where: Path, outputs: list[RunOutput], grader: str) -> None:
    for case_id in sorted({o.case for o in outputs}):
        reports = [(stem(o), o.report) for o in outputs if o.case == case_id]
        path = where / f"{case_id.replace('/', '-')}.rubric.yaml"
        path.write_text(json.dumps(rubric(load_case(CASES / case_id), reports, grader), indent=2))
        print(f"{EVAL} -c {path} -o {where / (path.stem + '.results.json')}")


def _refuse_collisions(out: Path, outputs: list[RunOutput]) -> None:
    names = [f"{stem(o)}{suffix}" for o in outputs for suffix in ("-report.md", ".toml")]
    taken = sorted(n for n in names if (out / n).exists())
    if taken:
        raise ValueError(f"{out} already has {', '.join(taken)}; extract into a fresh directory")


def _outside(out: Path, hidden: tuple[Path, ...]) -> None:
    """The work directory must not sit in a path the reviewer is denied."""
    for h in hidden:
        if out == h or h in out.parents:
            raise ValueError(f"--out {out} is inside {h}, which the reviewer may not read")


def _prompt_sha() -> str:
    """This repo's commit, refusing uncommitted prompt changes: a run must name its prompt."""
    if _git("status", "--porcelain", "--", "experts"):
        raise ValueError("experts/ has uncommitted changes; commit the variant first")
    return _git("rev-parse", "HEAD")


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _subagent_prompt(expert: str) -> str:
    files = [p["file"] for p in _manifest(expert)["prompt"] if p["kind"] == "claude-subagent"]
    if not files:
        raise ValueError(f"{expert} has no Claude subagent prompt")
    return (EXPERTS / expert / files[0]).read_text()


def _droppable(expert: str, drop: list[str]) -> frozenset[str]:
    """Sections to ablate, refusing unknown or required ones."""
    by_heading = {s["heading"]: s for s in _manifest(expert).get("section", [])}
    for heading in drop:
        section = by_heading.get(heading)
        if section is None:
            raise ValueError(f"{expert} declares no section {heading!r}")
        if section["required"]:
            raise ValueError(f"{expert}'s {heading!r} section is required and never ablated")
    return frozenset(drop)


def _manifest(expert: str) -> dict:
    with (EXPERTS / expert / "expert.toml").open("rb") as f:
        return tomllib.load(f)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m bench.runner", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)
    prep = sub.add_parser("prepare", help="blind checkout and review config")
    prep.set_defaults(command=prepare)
    prep.add_argument("case", type=Path, help="bench/cases/<expert>/<case>")
    prep.add_argument("--target", type=Path, required=True, help="a local clone of the target repo")
    prep.add_argument("--out", type=Path, required=True,
                      help="a fresh work directory outside this repo and the target clone")
    prep.add_argument("--variant", default="baseline", help="label for this prompt variant")
    prep.add_argument("--drop", action="append", default=[], metavar="HEADING",
                      help="ablate a section by its heading; repeatable")
    prep.add_argument("--repeat", type=int, default=3, help="runs per case")
    prep.add_argument("--model", help="reviewer model; default is the SDK's")
    prep.add_argument("--allow-domain", action="append", default=[], metavar="DOMAIN",
                      help="let the reviewer reach DOMAIN (repeatable); github.com exposes the "
                           "live review, so a run using it is not blind")
    ext = sub.add_parser("extract", help="per-run reports, skeletons and the rubric config")
    ext.set_defaults(command=extract)
    ext.add_argument("results", type=Path, help="promptfoo results.json from the review")
    ext.add_argument("--out", type=Path, required=True, help="reports/<expert>/<date>-<variant>/")
    ext.add_argument("--grader", default=GRADER, help="promptfoo provider grading recall")
    return p


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
