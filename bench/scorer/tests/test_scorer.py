import io
import tempfile
from contextlib import redirect_stderr, redirect_stdout
import unittest
from pathlib import Path

from bench.scorer.__main__ import main
from bench.scorer.model import BenchError, Case, parse_case, parse_run
from bench.scorer.report import render
from bench.scorer.score import score_run, summarise

SHA = "a" * 40
PR = {"expert": "mux", "repo": "o/r", "pr": 7, "round": 1, "base": SHA, "head": SHA, "title": "t"}


def key_finding(id: str, severity: str) -> dict:
    return {"id": id, "severity": severity, "title": id, "files": ["f.rs"], "fix": SHA,
            "summary": "s", "caught_if": "c"}


KEY = {"review": "https://example/r", "finding": [key_finding("K1", "P1"), key_finding("K2", "P2")]}


def case() -> Case:
    return parse_case("mux/7-r1", PR, KEY)


def run(*findings: dict, n: int = 1, variant: str = "base") -> dict:
    return {"case": "mux/7-r1", "variant": variant, "prompt_sha": SHA, "run": n,
            "finding": list(findings)}


def matched(id: str, key: str, quality: str = "caught", severity: str = "P2") -> dict:
    return {"id": id, "severity": severity, "title": id, "match": key, "quality": quality}


def triaged(id: str, triage: str) -> dict:
    return {"id": id, "severity": "P2", "title": id, "triage": triage, "reason": "r"}


class ParseTest(unittest.TestCase):
    def test_a_case_loads_its_key(self) -> None:
        self.assertEqual(case().key_ids(), {"K1", "K2"})

    def test_an_empty_key_is_refused(self) -> None:
        with self.assertRaisesRegex(BenchError, "no findings"):
            parse_case("mux/7-r1", PR, {"review": "r", "finding": []})

    def test_a_short_sha_is_refused(self) -> None:
        with self.assertRaisesRegex(BenchError, "40-character"):
            parse_case("mux/7-r1", {**PR, "head": "55d4bbf9"}, KEY)

    def test_a_case_in_another_experts_folder_is_refused(self) -> None:
        with self.assertRaisesRegex(BenchError, "does not match"):
            parse_case("conhost/7-r1", PR, KEY)

    def test_duplicate_key_ids_are_refused(self) -> None:
        with self.assertRaisesRegex(BenchError, "duplicate"):
            parse_case("mux/7-r1", PR, {"review": "r", "finding": [key_finding("K1", "P1")] * 2})

    def test_a_run_for_another_case_is_refused(self) -> None:
        with self.assertRaisesRegex(BenchError, "not 'mux/7-r1'"):
            parse_run({**run(), "case": "mux/8-r1"}, case())

    def test_a_match_outside_the_key_is_refused(self) -> None:
        with self.assertRaisesRegex(BenchError, "not in"):
            parse_run(run(matched("F1", "K9")), case())

    def test_a_finding_needs_exactly_one_grade(self) -> None:
        both = {**matched("F1", "K1"), "triage": "real", "reason": "r"}
        for finding in (both, {"id": "F1", "severity": "P2", "title": "t"}):
            with self.assertRaisesRegex(BenchError, "exactly one"):
                parse_run(run(finding), case())

    def test_an_unknown_severity_or_class_is_refused(self) -> None:
        with self.assertRaisesRegex(BenchError, "severity"):
            parse_run(run({**triaged("F1", "real"), "severity": "high"}), case())
        with self.assertRaisesRegex(BenchError, "triage"):
            parse_run(run(triaged("F1", "maybe")), case())


class ScoreTest(unittest.TestCase):
    def score(self, *findings: dict):
        return score_run(case(), parse_run(run(*findings), case()))

    def test_recall_counts_partial_as_half_and_takes_the_best_match(self) -> None:
        s = self.score(matched("F1", "K1", "partial"), matched("F2", "K1"), matched("F3", "K2", "partial"))
        self.assertEqual(s.hits, {"K1": 1.0, "K2": 0.5})
        self.assertEqual(s.recall, 0.75)

    def test_a_run_with_no_findings_has_zero_recall_and_no_precision(self) -> None:
        s = self.score()
        self.assertEqual(s.recall, 0.0)
        self.assertIsNone(s.precision)

    def test_precision_counts_fixed_since_as_true_and_ignores_design_questions(self) -> None:
        s = self.score(matched("F1", "K1"), triaged("F2", "fixed_since"), triaged("F3", "real"),
                       triaged("F4", "false_positive"), triaged("F5", "design_question"))
        self.assertEqual(s.precision, 0.75)

    def test_only_design_questions_leave_precision_undefined(self) -> None:
        self.assertIsNone(self.score(triaged("F1", "design_question")).precision)

    def test_severity_compares_the_best_match_with_the_key(self) -> None:
        s = self.score(matched("F1", "K1", "partial", "P0"), matched("F2", "K1", severity="P2"),
                       matched("F3", "K2", severity="P2"))
        self.assertEqual((s.severity.agree, s.severity.over, s.severity.under), (1, 0, 1))

    def test_an_ungraded_key_finding_counts_for_recall_but_not_severity(self) -> None:
        key = {"review": "r", "finding": [key_finding("K1", "ungraded")]}
        c = parse_case("mux/7-r1", PR, key)
        s = score_run(c, parse_run(run(matched("F1", "K1")), c))
        self.assertEqual(s.recall, 1.0)
        self.assertEqual(s.severity.agree + s.severity.over + s.severity.under, 0)
        self.assertIn("| K1 | ungraded |", render(c, summarise(c, [parse_run(run(), c)])))

    def test_a_summary_spreads_runs_and_rates_each_key_finding(self) -> None:
        runs = [parse_run(run(matched("F1", "K1"), triaged("F2", "false_positive"), n=1), case()),
                parse_run(run(matched("F1", "K2"), n=2), case())]
        s = summarise(case(), runs)
        self.assertEqual(s.recall, (0.5, 0.5, 0.5))
        self.assertEqual(s.precision, (0.75, 0.5, 1.0))
        self.assertEqual((s.hit_rate("K1"), s.hit_rate("K2")), (0.5, 0.5))
        self.assertEqual(s.triage["key"], 2)

    def test_a_summary_refuses_mixed_variants_or_no_runs(self) -> None:
        with self.assertRaisesRegex(ValueError, "mix"):
            summarise(case(), [parse_run(run(variant=v), case()) for v in ("a", "b")])
        with self.assertRaisesRegex(ValueError, "no runs"):
            summarise(case(), [])


class ReportTest(unittest.TestCase):
    def test_the_report_lists_each_key_finding(self) -> None:
        text = render(case(), summarise(case(), [parse_run(run(matched("F1", "K1")), case())]))
        self.assertIn("| K1 | P1 | K1 | 1.00 |", text)
        self.assertIn("| K2 | P2 | K2 | 0.00 |", text)
        self.assertIn("Precision: 1.00", text)


class CliTest(unittest.TestCase):
    def test_the_cli_scores_files_and_fails_on_a_bad_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            case_dir = Path(tmp) / "mux" / "7-r1"
            case_dir.mkdir(parents=True)
            (case_dir / "pr.toml").write_text(_toml(PR))
            (case_dir / "answer_key.toml").write_text(
                'review = "r"\n' + "".join(f"[[finding]]\n{_toml(k)}" for k in KEY["finding"]))
            good, bad = Path(tmp) / "good.toml", Path(tmp) / "bad.toml"
            good.write_text(_toml({k: v for k, v in run().items() if k != "finding"}))
            bad.write_text('case = "mux/8-r1"\n')
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                self.assertEqual(main([str(case_dir), str(good)]), 0)
                self.assertEqual(main([str(case_dir), str(bad)]), 1)
            self.assertIn("## mux/7-r1", out.getvalue())
            self.assertIn("not 'mux/7-r1'", err.getvalue())


def _toml(table: dict) -> str:
    def value(v: object) -> str:
        if isinstance(v, list):
            return "[" + ", ".join(value(x) for x in v) + "]"
        return str(v) if isinstance(v, int) else f'"{v}"'
    return "".join(f"{k} = {value(v)}\n" for k, v in table.items())


if __name__ == "__main__":
    unittest.main()
