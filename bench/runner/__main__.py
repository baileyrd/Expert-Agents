"""Bench runner.

  python -m bench.runner prepare <case-dir> --target <clone> --out <dir>
      Check the case out blind and write a promptfoo config for one prompt variant.
  python -m bench.runner extract <results.json> --out <dir>
      Write each run's report and a graded-run skeleton for grading.
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
from .config import build
from .extract import report_md, run_outputs, run_skeleton

ROOT = Path(__file__).resolve().parent.parent.parent
EXPERTS = ROOT / "experts"
GRADER = "anthropic:messages:claude-sonnet-5-5"


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.command(args) or 0
    except (BenchError, CheckoutError, ValueError, KeyError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


def prepare(args: argparse.Namespace) -> int:
    """Check out the case blind and write its promptfoo config, then print the eval command."""
    case = load_case(args.case)
    prior = load_case(args.case.parent / case.prior) if case.prior else None
    prompt = without(_subagent_prompt(case.expert), _droppable(case.expert, args.drop))
    out: Path = args.out.resolve()
    name = case.id.replace("/", "-")
    blind_checkout(case, args.target, out / "checkout" / name)
    config = build(case, prior, prompt, args.variant, _prompt_sha(), out / "checkout" / name,
                   args.grader, args.model)
    path = out / f"{name}.promptfooconfig.yaml"
    path.write_text(json.dumps(config, indent=2) + "\n")  # JSON is valid YAML
    print(f"npx --prefix bench/runner promptfoo eval --no-cache -c {path} "
          f"--repeat {args.repeat} -o {out / 'results.json'}")
    return 0


def extract(args: argparse.Namespace) -> int:
    """Write `<case>-<n>-report.md` per run, and `<case>-<n>.toml` for each that succeeded.

    A failed run keeps its report, naming the error, and gets no graded-run skeleton, so it
    is never scored as a review with no findings. Any failure makes the exit status 1.
    """
    results = json.loads(args.results.read_text())
    args.out.mkdir(parents=True, exist_ok=True)
    failed = 0
    for o in run_outputs(results):
        stem = args.out / f"{o.case.split('/')[1]}-{o.run}"
        Path(f"{stem}-report.md").write_text(report_md(o))
        if o.error:
            failed += 1
            print(f"FAILED {stem}-report.md: {o.error}", file=sys.stderr)
            continue
        Path(f"{stem}.toml").write_text(run_skeleton(o))
        print(f"{stem}-report.md")
    return 1 if failed else 0


def _prompt_sha() -> str:
    """This repo's commit, refusing uncommitted prompt changes: a run must name its prompt."""
    dirty = _git("status", "--porcelain", "--", "experts")
    if dirty:
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
    prep = sub.add_parser("prepare", help="blind checkout and promptfoo config")
    prep.set_defaults(command=prepare)
    prep.add_argument("case", type=Path, help="bench/cases/<expert>/<case>")
    prep.add_argument("--target", type=Path, required=True, help="a local clone of the target repo")
    prep.add_argument("--out", type=Path, required=True, help="a fresh work directory")
    prep.add_argument("--variant", default="baseline", help="label for this prompt variant")
    prep.add_argument("--drop", action="append", default=[], metavar="HEADING",
                      help="ablate a section by its heading; repeatable")
    prep.add_argument("--repeat", type=int, default=3, help="runs per case")
    prep.add_argument("--model", help="reviewer model; default is the SDK's")
    prep.add_argument("--grader", default=GRADER, help="promptfoo provider grading recall")
    ext = sub.add_parser("extract", help="per-run reports from a promptfoo results file")
    ext.set_defaults(command=extract)
    ext.add_argument("results", type=Path, help="promptfoo results.json")
    ext.add_argument("--out", type=Path, required=True, help="reports/<expert>/<date>-<variant>/")
    return p


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
