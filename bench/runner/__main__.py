"""Bench runner.

  python -m bench.runner prepare <case> --target <clone> --out <dir>
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
GRADE = "grade"  # in a work directory: results and rubrics, denied to the reviewer
RUNNER = ROOT / "bench" / "runner"
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
    case_dir = _case_dir(args.case)
    case = load_case(case_dir)
    prior = load_case(case_dir.parent / case.prior) if case.prior else None
    prompt = without(_subagent_prompt(case.expert), _droppable(case.expert, args.drop))
    out, target = args.out.resolve(), args.target.resolve()
    _outside(out, (ROOT, target))
    name = case.id.replace("/", "-")
    grade = out / GRADE
    hidden = (ROOT, target, grade)
    if args.repeat < 1:
        raise ValueError("--repeat must be at least 1")
    runs = tuple(Boundary(checkout=out / "review" / f"{name}-{n}",
                          scratch=out / "scratch" / f"{name}-{n}",
                          hidden=hidden, domains=tuple(args.allow_domain))
                 for n in range(1, args.repeat + 1))
    for run in runs:
        blind_checkout(case, target, run.checkout)
        run.scratch.mkdir(parents=True, exist_ok=True)
    grade.mkdir(exist_ok=True)
    _link_modules(out)
    config = review(case, prior, prompt, args.variant, _prompt_sha(), runs, args.model)
    path = out / f"{name}.review.yaml"
    path.write_text(json.dumps(config, indent=2) + "\n")  # JSON is valid YAML
    print(f"{EVAL} -c {path} -o {grade / 'results.json'}")
    return 0


def extract(args: argparse.Namespace) -> int:
    """Write `<case>-<n>-report.md` per run and `<case>-<n>.toml` for each that succeeded.

    Refuses before writing anything if a file it would write exists, so a re-run never
    replaces a graded run. A failed run keeps a report naming the error and gets no
    skeleton, so it is never scored as a review with no findings; it makes the exit
    status 1. The rubric config is written beside the results, for the successful runs.
    """
    results: Path = args.results.resolve()
    _require_grade_dir(results)
    _require_hidden(args.out.resolve(), (ROOT, results.parent))
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


def _require_grade_dir(results: Path) -> None:
    """Results, and the rubrics written beside them, stay in the grade dir `prepare` made.

    Every review config `prepare` writes denies that dir to the reviewer, so a later run of
    the review cannot read the rubrics.
    """
    work = results.parent.parent
    if results.parent.name != GRADE or not (work / "review").is_dir():
        raise ValueError(f"{results} is not in a prepared work directory's {GRADE}/; "
                         "use the -o path prepare printed")


def _require_hidden(out: Path, hidden: tuple[Path, ...]) -> None:
    """Graded runs name key findings, so they go only where every review is denied."""
    if not any(out == h or h in out.parents for h in hidden):
        raise ValueError(f"--out {out} is outside this repo and the grade dir; graded runs "
                         "there would be readable by a later review")


def _refuse_collisions(out: Path, outputs: list[RunOutput]) -> None:
    names = [f"{stem(o)}{suffix}" for o in outputs for suffix in ("-report.md", ".toml")]
    taken = sorted(n for n in names if (out / n).exists())
    if taken:
        raise ValueError(f"{out} already has {', '.join(taken)}; extract into a fresh directory")


def _case_dir(case: Path) -> Path:
    """A case directory, or a case id such as `mux/1251-r1` under bench/cases."""
    if case.is_dir() or case.is_absolute():
        return case
    return CASES / case


def _link_modules(out: Path) -> None:
    """promptfoo loads the Agent SDK from beside the config, so point there at the pinned one."""
    modules = RUNNER / "node_modules"
    link = out / "node_modules"
    if link.is_symlink() and link.resolve() == modules.resolve():
        return
    if link.exists() or link.is_symlink():
        raise ValueError(f"{link} exists and is not a link to {modules}")
    link.symlink_to(modules, target_is_directory=True)


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
    prep.add_argument("case", type=Path,
                      help="a case directory, or its id under bench/cases (mux/1251-r1)")
    prep.add_argument("--target", type=Path, required=True, help="a local clone of the target repo")
    prep.add_argument("--out", type=Path, required=True,
                      help="a fresh work directory outside this repo and the target clone")
    prep.add_argument("--variant", default="baseline", help="label for this prompt variant")
    prep.add_argument("--drop", action="append", default=[], metavar="HEADING",
                      help="ablate a section by its heading; repeatable")
    prep.add_argument("--repeat", type=int, default=3, help="runs per case, each in its own checkout")
    prep.add_argument("--model", help="reviewer model; default is the SDK's")
    prep.add_argument("--allow-domain", action="append", default=[], metavar="DOMAIN",
                      help="let sandboxed Bash reach DOMAIN (repeatable); the web tools stay "
                           "off. github.com exposes the live review, so a run using it is not blind")
    ext = sub.add_parser("extract", help="per-run reports, skeletons and the rubric config")
    ext.set_defaults(command=extract)
    ext.add_argument("results", type=Path, help="<work>/grade/results.json from the review")
    ext.add_argument("--out", type=Path, required=True, help="reports/<expert>/<date>-<variant>/")
    ext.add_argument("--grader", default=GRADER, help="promptfoo provider grading recall")
    return p


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
