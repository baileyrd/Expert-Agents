"""Fix-bench commands: measure a developer (developers/<name>/) on a case set.

A case set is a folder bench/fixcases/<set>/; every case in it names the same developer.
`rusty-tokio` replays past fixes; `rusty-tokio-seeded` plants blind bugs (bench/fix/seed.py).

  python3 -m bench.fix suite <set> [--model M] [--allow-domain D]... [--check]
      Write bench/suites/<set>-dev/: the Eval-Kit suite and the prompt spec.
  python3 -m bench.fix seed <set> --clone <target clone> [--write]
      Seeded sets: build each seeded commit in the clone and check it is the pinned base
      (--write pins it instead). Run before check, grade or the Eval-Kit run.
  python3 -m bench.fix check <set> --clone <target clone> --work <dir>
      Validate each case: its held-back tests fail at the base and pass at the fix.
  python3 -m bench.fix grade <set> --clone <target clone> --work <dir> --out <file.json>
      Execute every stored run (see bench/fix/grade.py) and write the grades.

Then run it like the reviewer bench (bench/README.md):
  python3 -m evalkit run bench/suites/<set>-dev/suite.toml --yes --jobs 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tomllib
from dataclasses import asdict
from pathlib import Path

from bench import extract as ex
from bench.__main__ import suite_jobs
from bench.fix import grade as gr
from bench.fix import sandbox
from bench.fix import seed as sd
from bench.fix.model import FixCase, parse_case, prompt_toml, suite_toml

ROOT = Path(__file__).resolve().parents[2]
DEVELOPERS = ROOT / "developers"
CASES = ROOT / "bench" / "fixcases"
SUITES = ROOT / "bench" / "suites"
# Each developer's suite command, run in the grading sandbox (bench/fix/sandbox.py). It
# must pass at every case base unmodified, or suite_green and red_at_base mean nothing.
# Skipped, rusty-tokio:
# - `udp_multicast`: v4 and v6 multicast tests need a multicast route, which the
#   sandbox's fresh network namespace lacks (v6 has one on some hosts, not on baileyAi);
# - `update_waker_after_shutdown_wakes_immediately_instead_of_hanging_forever`: hangs
#   intermittently at the rwlock-queue base 6c09ab3cf, unmodified (baileyAi, 2026-11-01).
SUITE = {"rusty-tokio": ["cargo", "test", "-p", "rusty_tokio", "--",
                         "--skip", "udp_multicast",
                         "--skip", "update_waker_after_shutdown_wakes_immediately_instead_of"
                         "_hanging_forever"]}
TEST_TIMEOUT_S = 1200
# The whole suite from a fresh target dir: a full debug build plus every test, on a box
# that may be running other jobs. 1200 s timed some of these out (2026-11-01).
SUITE_TIMEOUT_S = 3600


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
    found = cases(args.developer)
    developer = developer_of(found)
    prompt = (DEVELOPERS / developer / "prompt.md").read_text()
    files = {"suite.toml": suite_toml(args.developer, found, model, domains),
             "baseline.prompt.toml": prompt_toml(developer, prompt)}
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


def seed(args: argparse.Namespace) -> int:
    bad = 0
    for case in (c for c in cases(args.developer) if c.seeded):
        sha = sd.plant(case, args.clone.resolve())
        if sha == case.base:
            print(f"{case.id}: {sha} ok")
        elif args.write:
            _pin_base(case, sha)
            print(f"{case.id}: pinned base {sha}")
        else:
            bad += 1
            print(f"{case.id}: built {sha}, case pins {case.base}  MISMATCH")
    return 1 if bad else 0


def check(args: argparse.Namespace) -> int:
    bad = 0
    for case in cases(args.developer):
        base, fix = gr.check_case(case, args.clone.resolve(), _work(args), TEST_TIMEOUT_S)
        suite = gr.check_suite(case, args.clone.resolve(), _work(args), suite_for(case),
                               SUITE_TIMEOUT_S)
        ok = gr.valid(base, fix) and suite.passed
        bad += not ok
        print(f"{case.id}: at base {base.result}, at fix {fix.result}, suite at base "
              f"{suite.result}{'' if ok else '  INVALID'}")
        for step, out in (("base", base), ("fix", fix), ("suite", suite)):
            if out.detail:
                print(f"  {step}: " + out.detail.replace("\n", "\n    "))
    return 1 if bad else 0


def grade(args: argparse.Namespace) -> int:
    """Grade the current suite's runs only: each taken by its content key, all required."""
    by_id = {c.id.split("/", 1)[1]: c for c in cases(args.developer)}
    folder = SUITES / f"{args.developer}-dev"
    jobs = suite_jobs(folder / "suite.toml", args.variant)
    lines = (folder / ".evalkit" / "suite.jsonl").read_text().splitlines()
    records = ex.current_records(lines, jobs, args.variant)
    developer_of(list(by_id.values()))
    sandbox.bwrap()  # fail closed before any work
    grades = []
    for job, record in records:
        g = gr.grade_run(by_id[job.case], job.sample + 1, job.key,
                         record["trace"].get("files") or [], args.clone.resolve(),
                         _work(args), suite_for(by_id[job.case]), TEST_TIMEOUT_S,
                         SUITE_TIMEOUT_S)
        grades.append(g)
        print(f"{g.case} run {g.run}: solved {g.solved} (fixed {g.fixed}, suite green "
              f"{g.suite_green}, own test red at base {g.red_at_base}) {g.note}".rstrip())
        for step, why in g.failures.items():
            print(f"  {step}: " + why.replace("\n", "\n    "))
    suite = _toml(folder / "suite.toml")
    out = {"set": args.developer, "variant": args.variant,
           "model": suite["variant"][args.variant]["model"],
           "prompt_spec_sha256": _sha(folder / suite["variant"][args.variant]["subject"]),
           "grades": [{**asdict(g), "solved": g.solved} for g in grades]}
    args.out.write_text(json.dumps(out, indent=2) + "\n")
    return 0


def cases(case_set: str) -> list[FixCase]:
    folder = CASES / case_set
    if not folder.is_dir():
        raise ValueError(f"{case_set} has no fix cases under {folder}")
    return [_load(f"{case_set}/{d.name}", d) for d in sorted(folder.iterdir()) if d.is_dir()]


def suite_for(case: FixCase) -> list[str]:
    """The developer's suite command, minus the existing tests this case's defect breaks."""
    skips = [arg for name in case.suite_skip for arg in ("--skip", name)]
    return [*SUITE[developer_of([case])], *skips]


def developer_of(found: list[FixCase]) -> str:
    names = {c.developer for c in found}
    if len(names) != 1 or "" in names:
        raise ValueError(f"a case set names exactly one developer, found {sorted(names)}")
    return names.pop()


def _load(case_id: str, folder: Path) -> FixCase:
    raw = _toml(folder / "case.toml")
    if not raw.get("seed"):
        return parse_case(case_id, raw)
    hidden = {p: (folder / "held_back" / p).read_text() for p in raw.get("held_back") or []}
    return parse_case(case_id, raw, (folder / raw["seed"]).read_text(), hidden)


def _pin_base(case: FixCase, sha: str) -> None:
    path = CASES / case.id / "case.toml"
    text, n = re.subn(r'(?m)^base = "[0-9a-f]{40}"$', f'base = "{sha}"', path.read_text())
    if n != 1:
        raise ValueError(f"{path}: expected one base line")
    path.write_text(text)


def _work(args: argparse.Namespace) -> Path:
    work = args.work.resolve()
    if ROOT in work.parents or work == ROOT:
        raise ValueError("--work must be outside this repo")
    work.mkdir(parents=True, exist_ok=True)
    return work


def _domains(current: dict) -> tuple[str, ...]:
    text = current.get("settings")
    return tuple(json.loads(text)["sandbox"]["network"]["allowedDomains"]) if text else ()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def _toml(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m bench.fix", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)
    s = sub.add_parser("suite", help="generate a developer's Eval-Kit suite")
    s.set_defaults(command=write_suite)
    s.add_argument("developer", metavar="set")
    s.add_argument("--model")
    s.add_argument("--allow-domain", action="append", default=[], metavar="DOMAIN")
    s.add_argument("--check", action="store_true")
    d = sub.add_parser("seed", help="build seeded commits in the clone")
    d.set_defaults(command=seed)
    d.add_argument("developer", metavar="set")
    d.add_argument("--clone", type=Path, required=True, help="the target repo clone")
    d.add_argument("--write", action="store_true", help="pin the built SHA as each case's base")
    for name, fn, helptext in (("check", check, "validate the cases"),
                               ("grade", grade, "execute and grade stored runs")):
        c = sub.add_parser(name, help=helptext)
        c.set_defaults(command=fn)
        c.add_argument("developer", metavar="set")
        c.add_argument("--clone", type=Path, required=True, help="the target repo clone")
        c.add_argument("--work", type=Path, required=True, help="scratch dir outside this repo")
        if name == "grade":
            c.add_argument("--variant", default="baseline")
            c.add_argument("--out", type=Path, required=True)
    return p


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
