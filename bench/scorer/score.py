"""Recall, precision and severity agreement for graded runs. Pure functions.

- Recall: each key finding scores its best match (caught 1, partial 0.5, missed 0),
  averaged over the key.
- Precision: true positives over true plus false positives. Key matches, `real` and
  `fixed_since` findings are true positives (each was real at the reviewed head);
  design questions count on neither side.
- Severity: each matched key finding compares the reviewer's grade on its best match
  with the key's. "Over" means the reviewer graded it more severe. Ungraded key
  findings are left out.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import mean

from .model import Case, Match, Quality, Run, RunFinding, Triage, Triaged

_TRUE = frozenset({Triage.REAL, Triage.FIXED_SINCE})


@dataclass(frozen=True)
class SeverityAgreement:
    agree: int = 0
    over: int = 0
    under: int = 0

    def __add__(self, other: SeverityAgreement) -> SeverityAgreement:
        return SeverityAgreement(
            self.agree + other.agree, self.over + other.over, self.under + other.under
        )


@dataclass(frozen=True)
class RunScore:
    run: int
    recall: float
    precision: float | None  # None when nothing counts toward it
    hits: dict[str, float]  # key id -> 1.0 caught, 0.5 partial, 0.0 missed
    severity: SeverityAgreement
    triage: Counter[str]  # "key" plus each Triage value


@dataclass(frozen=True)
class Summary:
    case: str
    variant: str
    runs: tuple[RunScore, ...]

    @property
    def recall(self) -> tuple[float, float, float]:
        return _spread([r.recall for r in self.runs])

    @property
    def precision(self) -> tuple[float, float, float] | None:
        values = [r.precision for r in self.runs if r.precision is not None]
        return _spread(values) if values else None

    def hit_rate(self, key_id: str) -> float:
        return mean(r.hits[key_id] for r in self.runs)

    @property
    def severity(self) -> SeverityAgreement:
        return sum((r.severity for r in self.runs), SeverityAgreement())

    @property
    def triage(self) -> Counter[str]:
        return sum((r.triage for r in self.runs), Counter())


def score_run(case: Case, run: Run) -> RunScore:
    """Score one graded run against its case's answer key."""
    best = _best_matches(run.findings)
    hits = {k.id: best[k.id][0].value if k.id in best else 0.0 for k in case.key}
    return RunScore(
        run=run.run,
        recall=mean(hits.values()),
        precision=_precision(run.findings),
        hits=hits,
        severity=_severity(case, best),
        triage=Counter(_label(f) for f in run.findings),
    )


def summarise(case: Case, runs: list[Run]) -> Summary:
    """Score N runs of one variant on one case; N > 1 separates signal from noise."""
    if not runs:
        raise ValueError(f"{case.id}: no runs to summarise")
    variants = {r.variant for r in runs}
    if len(variants) != 1:
        raise ValueError(f"{case.id}: runs mix variants {sorted(variants)}")
    return Summary(case.id, variants.pop(), tuple(score_run(case, r) for r in runs))


def _best_matches(findings: tuple[RunFinding, ...]) -> dict[str, tuple[Quality, RunFinding]]:
    best: dict[str, tuple[Quality, RunFinding]] = {}
    for f in findings:
        if not isinstance(f.grade, Match):
            continue
        held = best.get(f.grade.key_id)
        if held is None or f.grade.quality.value > held[0].value:
            best[f.grade.key_id] = (f.grade.quality, f)
    return best


def _precision(findings: tuple[RunFinding, ...]) -> float | None:
    true = sum(1 for f in findings if _is_true(f))
    false = sum(1 for f in findings if _is(f, Triage.FALSE_POSITIVE))
    return true / (true + false) if true + false else None


def _severity(case: Case, best: dict[str, tuple[Quality, RunFinding]]) -> SeverityAgreement:
    agree = over = under = 0
    for key in case.key:
        if key.id not in best or key.severity is None:
            continue
        given = best[key.id][1].severity.value
        if given == key.severity.value:
            agree += 1
        elif given < key.severity.value:
            over += 1
        else:
            under += 1
    return SeverityAgreement(agree, over, under)


def _is_true(f: RunFinding) -> bool:
    return isinstance(f.grade, Match) or (
        isinstance(f.grade, Triaged) and f.grade.triage in _TRUE
    )


def _is(f: RunFinding, triage: Triage) -> bool:
    return isinstance(f.grade, Triaged) and f.grade.triage is triage


def _label(f: RunFinding) -> str:
    return "key" if isinstance(f.grade, Match) else f.grade.triage.value


def _spread(values: list[float]) -> tuple[float, float, float]:
    return mean(values), min(values), max(values)
