"""File adapter: reads cases and runs from TOML on disk into bench records."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from .model import BenchError, Case, Run, parse_case, parse_run


def load_case(case_dir: Path) -> Case:
    """Load `<expert>/<case>/` holding `pr.toml` and `answer_key.toml`."""
    case_id = f"{case_dir.parent.name}/{case_dir.name}"
    return parse_case(case_id, _toml(case_dir / "pr.toml"), _toml(case_dir / "answer_key.toml"))


def load_run(path: Path, case: Case) -> Run:
    return parse_run(_toml(path), case)


def _toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as err:
        raise BenchError(f"{path}: {err}") from err
