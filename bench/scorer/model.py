"""Bench records: cases, answer keys and graded runs, validated at the boundary.

Parsing takes plain dicts (as `tomllib` returns them) so this module does no I/O.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

_SHA = re.compile(r"^[0-9a-f]{40}$")
UNGRADED = "ungraded"  # answer keys only: blocking in the review, with no P-grade


class BenchError(ValueError):
    """A case, answer key or run file that does not follow the bench format."""


class Severity(Enum):
    P0 = 0
    P1 = 1
    P2 = 2
    P3 = 3


class Quality(Enum):
    """How well a reviewer finding covers an answer-key finding."""

    CAUGHT = 1.0
    PARTIAL = 0.5


class Triage(Enum):
    """A verifier's class for a finding that is not in the answer key."""

    REAL = "real"
    FIXED_SINCE = "fixed_since"  # real at the reviewed head, gone on main since
    DESIGN_QUESTION = "design_question"  # the behaviour is documented as intended
    FALSE_POSITIVE = "false_positive"


@dataclass(frozen=True)
class KeyFinding:
    id: str
    severity: Severity | None  # None: the review called it blocking without a grade
    title: str
    files: tuple[str, ...]
    fix: str
    summary: str
    caught_if: str


@dataclass(frozen=True)
class Case:
    id: str  # "<expert>/<dir>", e.g. "mux/1251-r1"
    expert: str
    repo: str
    pr: int
    round: int
    base: str
    head: str
    title: str
    prior: str | None
    review: str
    key: tuple[KeyFinding, ...]

    def key_ids(self) -> frozenset[str]:
        return frozenset(k.id for k in self.key)


@dataclass(frozen=True)
class Match:
    key_id: str
    quality: Quality


@dataclass(frozen=True)
class Triaged:
    triage: Triage
    reason: str


Grade = Match | Triaged


@dataclass(frozen=True)
class RunFinding:
    id: str
    severity: Severity
    title: str
    grade: Grade


@dataclass(frozen=True)
class Run:
    case: str
    variant: str
    prompt_sha: str
    run: int
    findings: tuple[RunFinding, ...]


def parse_case(case_id: str, pr: dict[str, Any], key: dict[str, Any]) -> Case:
    """Build a case from its `pr.toml` and `answer_key.toml` tables."""
    findings = tuple(_parse_key_finding(f) for f in _list(key, "finding"))
    if not findings:
        raise BenchError(f"{case_id}: answer key has no findings")
    _unique(case_id, [f.id for f in findings])
    expert = _str(pr, "expert")
    if not case_id.startswith(f"{expert}/"):
        raise BenchError(f"{case_id}: expert {expert!r} does not match its folder")
    return Case(
        id=case_id,
        expert=expert,
        repo=_str(pr, "repo"),
        pr=_int(pr, "pr"),
        round=_int(pr, "round"),
        base=_sha(pr, "base"),
        head=_sha(pr, "head"),
        title=_str(pr, "title"),
        prior=_opt_str(pr, "prior"),
        review=_str(key, "review"),
        key=findings,
    )


def parse_run(raw: dict[str, Any], case: Case) -> Run:
    """Build a graded run and check it against the case it claims to grade."""
    case_id = _str(raw, "case")
    if case_id != case.id:
        raise BenchError(f"run is for {case_id!r}, not {case.id!r}")
    findings = tuple(_parse_run_finding(f, case) for f in _list(raw, "finding"))
    _unique(case_id, [f.id for f in findings])
    return Run(
        case=case_id,
        variant=_str(raw, "variant"),
        prompt_sha=_sha(raw, "prompt_sha"),
        run=_int(raw, "run"),
        findings=findings,
    )


def _parse_key_finding(raw: dict[str, Any]) -> KeyFinding:
    ungraded = raw.get("severity") == UNGRADED
    return KeyFinding(
        id=_str(raw, "id"),
        severity=None if ungraded else _severity(raw),
        title=_str(raw, "title"),
        files=tuple(_strs(raw, "files")),
        fix=_sha(raw, "fix"),
        summary=_str(raw, "summary").strip(),
        caught_if=_str(raw, "caught_if"),
    )


def _parse_run_finding(raw: dict[str, Any], case: Case) -> RunFinding:
    return RunFinding(
        id=_str(raw, "id"),
        severity=_severity(raw),
        title=_str(raw, "title"),
        grade=_parse_grade(raw, case),
    )


def _parse_grade(raw: dict[str, Any], case: Case) -> Grade:
    match, triage = raw.get("match"), raw.get("triage")
    if (match is None) == (triage is None):
        raise BenchError(f"finding {raw.get('id')!r}: give exactly one of match or triage")
    if triage is not None:
        return Triaged(_enum(Triage, triage, "triage"), _str(raw, "reason"))
    if match not in case.key_ids():
        raise BenchError(f"finding {raw.get('id')!r}: {match!r} is not in {case.id}'s key")
    quality = _enum(Quality, str(raw.get("quality", "")).upper(), "quality", by_name=True)
    return Match(match, quality)


def _severity(raw: dict[str, Any]) -> Severity:
    return _enum(Severity, _str(raw, "severity"), "severity", by_name=True)


def _enum(cls: Any, value: Any, field: str, by_name: bool = False) -> Any:
    try:
        return cls[value] if by_name else cls(value)
    except (KeyError, ValueError) as err:
        raise BenchError(f"{field}: {value!r} is not one of {[m.name for m in cls]}") from err


def _str(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BenchError(f"{field}: expected a non-empty string, got {value!r}")
    return value


def _opt_str(raw: dict[str, Any], field: str) -> str | None:
    return None if raw.get(field) is None else _str(raw, field)


def _strs(raw: dict[str, Any], field: str) -> list[str]:
    value = raw.get(field)
    if not isinstance(value, list) or not value or not all(isinstance(v, str) for v in value):
        raise BenchError(f"{field}: expected a non-empty list of strings, got {value!r}")
    return value


def _int(raw: dict[str, Any], field: str) -> int:
    value = raw.get(field)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BenchError(f"{field}: expected a positive integer, got {value!r}")
    return value


def _sha(raw: dict[str, Any], field: str) -> str:
    value = _str(raw, field)
    if not _SHA.match(value):
        raise BenchError(f"{field}: expected a full 40-character commit SHA, got {value!r}")
    return value


def _list(raw: dict[str, Any], field: str) -> list[dict[str, Any]]:
    value = raw.get(field, [])
    if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
        raise BenchError(f"{field}: expected an array of tables")
    return value


def _unique(where: str, ids: list[str]) -> None:
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise BenchError(f"{where}: duplicate finding ids {dupes}")
