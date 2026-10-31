"""Fix-bench commands: measure a developer (developers/<name>/) on past fixes.

  python3 -m bench.fix suite <developer> [--model M] [--allow-domain D]... [--check]
      Write bench/suites/<developer>-dev/: the Eval-Kit suite and the prompt spec.
  python3 -m bench.fix check <developer> --clone <target clone> --work <dir>
      Validate each case: its held-back tests fail at the base and pass at the fix.
  python3 -m bench.fix grade <developer> --clone <target clone> --work <dir> --out <file.json>
      Execute every stored run (see bench/fix/grade.py) and write the grades.

Then run it like the reviewer bench (bench/README.md):
  python3 -m evalkit run bench/suites/<developer>-dev/suite.toml --yes --jobs 2
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import asdict
from pathlib import Path

from bench.fix import grade as gr
from bench.fix.model import FixCase, parse_case, prompt_toml, suite_toml

ROOT = Path(__file__).resolve().parents[2]
DEVELOPERS = ROOT / "developers"
CASES = ROOT / "bench" / "fixcases"
SUITES = ROOT / "bench" / "suites"
CRATE = {"rusty-tokio": "rusty_tokio"}
TEST_TIMEOUT_S = 1200


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.command(args)
    except (ValueError, KeyError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


def write_suite(args: argparse.Namespace) -> int:
    out = SUITES / f"{args.developer}-dev"
    current = _toml(out / "suite.toml") if (out / "suite.toml").exists() else {}
    model = args.model or current.get("variant", {}).get("baseline", {}).get("model")
    if not model:
        raise ValueError("--model is required for a new suite; it is recorded in the suite")
    domains = tuple(args.allow_domain) if args.allow_domain else _domains(current)
    prompt = (DEVELOPERS / args.developer / "prompt.md").read_text()
    files = {"suite.toml": suite_toml(args.developer, cases(args.developer), model, domains),
             "baseline.prompt.toml": prompt_toml(args.developer, prompt)}
    if args.check:
        stale = sorted(n for n, t in files.items()
                       if not (out / n).exists() or (out / n).read_text() != t)
        for name in stale:
            print(f"{out / name} is stale; run python3 -m bench.fix suite {args.developer}",
                  file=sys.stderr)
        return 1 if stale else 0
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text)
    print(f"wrote {out}/suite.toml and baseline.prompt.toml")
    return 0


def check(args: argparse.Namespace) -> int:
    bad = 0
    for case in cases(args.developer):
        red, green = gr.check_case(case, args.clone.resolve(), _work(args), TEST_TIMEOUT_S)
        ok = red and green
        bad += not ok
        print(f"{case.id}: red at base {red}, green at fix {green}{'' if ok else '  INVALID'}")
    return 1 if bad else 0


def grade(args: argparse.Namespace) -> int:
    by_id = {c.id.split("/", 1)[1]: c for c in cases(args.developer)}
    store = SUITES / f"{args.developer}-dev" / ".evalkit" / "suite.jsonl"
    grades = []
    for (case_id, run), files in sorted(gr.store_runs(store, args.variant).items()):
        g = gr.grade_run(by_id[case_id], run, files, args.clone.resolve(), _work(args),
                         CRATE[args.developer], TEST_TIMEOUT_S)
        grades.append(g)
        print(f"{g.case} run {g.run}: solved {g.solved} (fixed {g.fixed}, suite green "
              f"{g.suite_green}, own test red at base {g.red_at_base}) {g.note}".rstrip())
    args.out.write_text(json.dumps([{**asdict(g), "solved": g.solved} for g in grades], indent=2) + "\n")
    return 0


def cases(developer: str) -> list[FixCase]:
    folder = CASES / developer
    if not folder.is_dir():
        raise ValueError(f"{developer} has no fix cases under {folder}")
    return [parse_case(f"{developer}/{d.name}", _toml(d / "case.toml"))
            for d in sorted(folder.iterdir()) if d.is_dir()]


def _work(args: argparse.Namespace) -> Path:
    work = args.work.resolve()
    if ROOT in work.parents or work == ROOT:
        raise ValueError("--work must be outside this repo")
    work.mkdir(parents=True, exist_ok=True)
    return work


def _domains(current: dict) -> tuple[str, ...]:
    text = current.get("settings")
    return tuple(json.loads(text)["sandbox"]["network"]["allowedDomains"]) if text else ()


def _toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m bench.fix", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)
    s = sub.add_parser("suite", help="generate a developer's Eval-Kit suite")
    s.set_defaults(command=write_suite)
    s.add_argument("developer")
    s.add_argument("--model")
    s.add_argument("--allow-domain", action="append", default=[], metavar="DOMAIN")
    s.add_argument("--check", action="store_true")
    for name, fn, helptext in (("check", check, "validate the cases"),
                               ("grade", grade, "execute and grade stored runs")):
        c = sub.add_parser(name, help=helptext)
        c.set_defaults(command=fn)
        c.add_argument("developer")
        c.add_argument("--clone", type=Path, required=True, help="the target repo clone")
        c.add_argument("--work", type=Path, required=True, help="scratch dir outside this repo")
        if name == "grade":
            c.add_argument("--variant", default="baseline")
            c.add_argument("--out", type=Path, required=True)
    return p


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
