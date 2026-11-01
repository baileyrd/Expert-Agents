"""Bench commands around Eval-Kit.

  python3 -m bench suite <expert> [--model M] [--allow-domain D]... [--check]
      Write bench/suites/<expert>/: the Eval-Kit suite and the prompt spec.
  python3 -m bench extract <expert> --out reports/<expert>/<date>-<variant>/
      Write each stored run's report and a graded-run skeleton for bench.scorer.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from pathlib import Path

from bench import extract as ex
from bench import suite
from bench.scorer.load import load_case
from bench.scorer.model import BenchError, Case

ROOT = Path(__file__).resolve().parent.parent
EXPERTS = ROOT / "experts"
CASES = ROOT / "bench" / "cases"
SUITES = ROOT / "bench" / "suites"


def main(argv: list[str]) -> int:
    args = _parser().parse_args(argv)
    try:
        return args.command(args)
    except (BenchError, ValueError, KeyError, OSError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1


def write_suite(args: argparse.Namespace) -> int:
    """Generate the suite and prompt spec; with --check, only report whether they are current."""
    out = SUITES / args.expert
    current = _read_suite(out)
    model = args.model or current.get("variant", {}).get("baseline", {}).get("model")
    if not model:
        raise ValueError("--model is required for a new suite; it is recorded in the suite")
    domains = tuple(args.allow_domain) if args.allow_domain else _domains(current)
    files = generated(args.expert, model, domains)
    if args.check:
        stale = sorted(n for n, text in files.items() if _read(out / n) != text)
        for name in stale:
            print(f"{out / name} is stale; run python3 -m bench suite {args.expert}", file=sys.stderr)
        return 1 if stale else 0
    out.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (out / name).write_text(text)
    print(f"wrote {out}/suite.toml and baseline.prompt.toml")
    return 0


def generated(expert: str, model: str, domains: tuple[str, ...]) -> dict[str, str]:
    """The files `suite` writes for an expert, by name."""
    manifest = _manifest(expert)
    files = [p["file"] for p in manifest["prompt"] if p["kind"] == "claude-subagent"]
    if not files:
        raise ValueError(f"{expert} has no Claude subagent prompt")
    roles = {s["heading"]: s["role"] for s in manifest.get("section", [])}
    parts = suite.prompt_sections((EXPERTS / expert / files[0]).read_text(), roles)
    return {"suite.toml": suite.suite_toml(expert, _cases(expert), model, domains),
            "baseline.prompt.toml": suite.prompt_toml(expert, parts)}


def write_reports(args: argparse.Namespace) -> int:
    """Write `<case>-<n>-report.md` and `<case>-<n>.toml` per stored run of the variant.

    Refuses before writing anything if a file it would write exists, so a re-run never
    replaces a graded run.
    """
    out: Path = args.out.resolve()
    if ROOT not in out.parents:
        raise ValueError(f"--out {out} is outside this repo, where a later review could read it")
    folder = SUITES / args.expert
    variant = _read_suite(folder).get("variant", {}).get(args.variant)
    if variant is None:
        raise ValueError(f"{folder}/suite.toml has no variant {args.variant!r}")
    store = folder / ".evalkit" / "suite.jsonl"
    if not store.exists():
        raise ValueError(f"{store} does not exist; run evalkit first")
    # The model and prompt are part of each job's key, so a run found under it was made
    # by this model and by the prompt committed at this repo's (clean) HEAD.
    outputs = ex.run_outputs(store.read_text().splitlines(), args.expert, args.variant,
                             str(variant.get("model") or "unrecorded"), _prompt_sha(),
                             suite_jobs(folder / "suite.toml", args.variant))
    taken = sorted(n for o in outputs for n in (f"{ex.stem(o)}-report.md", f"{ex.stem(o)}.toml")
                   if (out / n).exists())
    if taken:
        raise ValueError(f"{out} already has {', '.join(taken)}; extract into a fresh directory")
    out.mkdir(parents=True, exist_ok=True)
    for o in outputs:
        (out / f"{ex.stem(o)}-report.md").write_text(ex.report_md(o))
        (out / f"{ex.stem(o)}.toml").write_text(ex.run_skeleton(o))
    print(f"wrote {len(outputs)} reports to {out}")
    return 0


def suite_jobs(suite_toml: Path, variant: str) -> tuple[ex.Job, ...]:
    """The variant's jobs and their keys, computed by Eval-Kit itself so they cannot drift."""
    try:
        from evalkit import plan, spec
    except ModuleNotFoundError:
        raise ValueError("needs the Eval-Kit that ran the suite on PYTHONPATH, "
                         "to compute each run's content key") from None
    jobs = plan.jobs(spec.load_suite(suite_toml))
    found = tuple(ex.Job(j.key, j.case.id, j.sample) for j in jobs if j.variant.id == variant)
    if not found:
        raise ValueError(f"{suite_toml} has no jobs for variant {variant!r}")
    return found


def _cases(expert: str) -> list[Case]:
    folder = CASES / expert
    if not folder.is_dir():
        raise ValueError(f"{expert} has no cases under {folder}")
    return [load_case(d) for d in sorted(folder.iterdir()) if d.is_dir()]


def _domains(current: dict) -> tuple[str, ...]:
    """The network domains the committed suite opens, so a regeneration keeps them."""
    text = current.get("settings")
    if not text:
        return ()
    return tuple(json.loads(text)["sandbox"]["network"]["allowedDomains"])


def _read_suite(folder: Path) -> dict:
    path = folder / "suite.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


def _read(path: Path) -> str | None:
    return path.read_text() if path.exists() else None


def _manifest(expert: str) -> dict:
    with (EXPERTS / expert / "expert.toml").open("rb") as f:
        return tomllib.load(f)


def _prompt_sha() -> str:
    """This repo's commit, refusing uncommitted prompt changes: a report must name its prompt."""
    if _git("status", "--porcelain", "--", "experts", "bench/suites"):
        raise ValueError("experts/ or bench/suites/ has uncommitted changes; commit them first")
    return _git("rev-parse", "HEAD")


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ValueError(f"git {args[0]} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python3 -m bench", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(required=True)
    gen = sub.add_parser("suite", help="generate an expert's Eval-Kit suite")
    gen.set_defaults(command=write_suite)
    gen.add_argument("expert")
    gen.add_argument("--model", help="reviewer model; defaults to the committed suite's")
    gen.add_argument("--allow-domain", action="append", default=[], metavar="DOMAIN",
                     help="let sandboxed Bash reach DOMAIN (repeatable); defaults to the "
                          "committed suite's. github.com exposes the live review")
    gen.add_argument("--check", action="store_true", help="exit 1 if the committed files are stale")
    ext = sub.add_parser("extract", help="per-run reports and graded-run skeletons")
    ext.set_defaults(command=write_reports)
    ext.add_argument("expert")
    ext.add_argument("--variant", default="baseline")
    ext.add_argument("--out", type=Path, required=True, help="reports/<expert>/<date>-<variant>/")
    return p


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
