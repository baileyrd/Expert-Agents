"""Score graded runs: python -m bench.scorer <case-dir> <run.toml>..."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .load import load_case, load_run
from .model import BenchError
from .report import render
from .score import summarise


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m bench.scorer", description=__doc__)
    parser.add_argument("case", type=Path, help="bench/cases/<expert>/<case>")
    parser.add_argument("runs", type=Path, nargs="+", help="graded run files")
    args = parser.parse_args(argv)
    try:
        case = load_case(args.case)
        summary = summarise(case, [load_run(p, case) for p in args.runs])
    except (BenchError, ValueError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    sys.stdout.write(render(case, summary))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
