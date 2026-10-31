import io
import json
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from bench import extract, suite
from bench.__main__ import CASES, EXPERTS, ROOT, SUITES, generated, main
from bench.prompt import body
from bench.scorer.load import load_case
from bench.scorer.model import parse_case, parse_run

SHA = "a" * 40


def case(round: int = 1, prior: str | None = None):
    pr = {"expert": "mux", "repo": "o/r", "pr": 7, "round": round, "base": SHA, "head": "b" * 40,
          "title": "t", **({"prior": prior} if prior else {})}
    key = {"review": "r", "finding": [
        {"id": "K1", "severity": "P1", "title": "Lost token", "files": ["f.rs"], "fix": SHA,
         "summary": "The retry is refused.", "caught_if": "Names the refused retry."},
        {"id": "K2", "severity": "ungraded", "title": "Missing test", "files": ["f.rs"],
         "fix": SHA, "summary": "s", "caught_if": "Names it."},
    ]}
    return parse_case(f"mux/7-r{round}", pr, key)


def experts_with_cases() -> list[str]:
    return sorted(p.name for p in CASES.iterdir() if p.is_dir())


class SuiteTest(unittest.TestCase):
    def test_every_expert_with_cases_has_a_current_suite(self) -> None:
        for expert in experts_with_cases():
            with self.subTest(expert):
                status, err = run_main(["suite", expert, "--check"])
                self.assertEqual(status, 0, err)

    def test_nothing_the_reviewer_reads_holds_its_own_answer_key(self) -> None:
        """A case's input, the prompt and the settings name none of its key; a re-review's
        input names the prior round's findings on purpose, by id and title only."""
        for expert in experts_with_cases():
            data = tomllib.loads((SUITES / expert / "suite.toml").read_text())
            shared = (SUITES / expert / "baseline.prompt.toml").read_text() + data["settings"]
            inputs = {c["id"]: c["input"] for c in data["case"]}
            for case_dir in sorted((CASES / expert).iterdir()):
                seen = shared + inputs[case_dir.name]
                for k in load_case(case_dir).key:
                    for secret in (k.title, k.summary.strip(), k.caught_if, k.fix):
                        self.assertNotIn(secret, seen, f"{expert} {case_dir.name} {k.id}")

    def test_the_prompt_spec_is_the_whole_prompt_by_section(self) -> None:
        for expert in experts_with_cases():
            spec = tomllib.loads(generated(expert, "m", ())["baseline.prompt.toml"])
            prompt = (EXPERTS / expert / "prompt.md").read_text()
            self.assertEqual("".join(s["text"] for s in spec["section"]), body(prompt))
            self.assertEqual(spec["tools"], ["Read", "Grep", "Glob", "Bash"])

    def test_bash_is_sandboxed_or_not_run_and_hidden_paths_are_unreadable(self) -> None:
        settings = json.loads(suite.settings(()))
        box = settings["sandbox"]
        self.assertEqual((box["enabled"], box["failIfUnavailable"], box["allowUnsandboxedCommands"]),
                         (True, True, False))
        self.assertEqual(box["filesystem"]["denyRead"], ["${EXPERT_AGENTS}", "${TARGET}", "~/.claude"])
        deny = settings["permissions"]["deny"]
        self.assertTrue({"Edit", "Write", "WebFetch", "WebSearch", "Read(/${EXPERT_AGENTS}/**)",
                         "Read(/${TARGET}/**)", "Read(~/.claude/**)"} <= set(deny))

    def test_network_is_off_unless_domains_are_allowed(self) -> None:
        self.assertEqual(json.loads(suite.settings(()))["sandbox"]["network"]["allowedDomains"], [])
        opened = json.loads(suite.settings(("static.crates.io",)))
        self.assertEqual(opened["sandbox"]["network"]["allowedDomains"], ["static.crates.io"])
        self.assertIn("WebFetch", opened["permissions"]["deny"])

    def test_each_key_finding_is_one_judge_check_and_the_case_starts_from_its_head(self) -> None:
        data = tomllib.loads(suite.suite_toml("mux", [case()], "m"))
        (c,) = data["case"]
        self.assertEqual([j["judge"].splitlines()[0] for j in c["checks"]],
                         ["K1: Lost token", "K2: Missing test"])
        self.assertIn("names the refused retry.", c["checks"][0]["judge"])
        self.assertEqual(c["checkout"], {"from": "${TARGET}", "head": "b" * 40, "base": SHA,
                                         "ref": "refs/pull/7/head"})
        self.assertEqual(data["variant"]["baseline"], {"subject": "baseline.prompt.toml", "model": "m"})

    def test_a_re_review_lists_the_prior_findings_and_a_first_review_none(self) -> None:
        first, second = case(), case(round=2, prior="7-r1")
        self.assertNotIn("Prior findings", suite.task(first, None))
        text = suite.task(second, first)
        self.assertIn("- K1 (P1): Lost token", text)
        self.assertIn("- K2 (blocking): Missing test", text)

    def test_an_undeclared_section_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "not declared"):
            suite.prompt_sections("---\nname: x\n---\nYou review.\n\n## Odd\n\nx\n", {"": "persona"})


def record(case_id: str, sample: int, text: str, variant: str = "baseline") -> str:
    trace = {"final_text": text, "tool_calls": [], "skills_invoked": [], "files": [],
             "input_tokens": 1000, "output_tokens": 200, "cost_usd": 1.5, "duration_ms": 61000}
    return json.dumps({"key": f"{case_id}{sample}{text}", "variant": variant, "case": case_id,
                       "sample": sample, "cli_version": "2.1.316", "trace": trace})


class ExtractTest(unittest.TestCase):
    LINES = [
        record("7-r1", 1, "second"),
        record("7-r1", 0, "old"),
        json.dumps({"kind": "verdict", "key": "k", "passed": True, "reason": "r"}),
        record("7-r1", 0, "new"),
        record("7-r1", 0, "other variant", variant="v2"),
    ]

    def test_the_latest_run_per_sample_wins_and_verdicts_are_skipped(self) -> None:
        outs = extract.run_outputs(self.LINES, "mux", "baseline", "m", SHA)
        self.assertEqual([(o.run, o.report) for o in outs], [(1, "new"), (2, "second")])
        self.assertIn("model m, 2.1.316; 61 s, 1200 tokens, $1.50 -->", extract.report_md(outs[0]))

    def test_the_skeleton_is_a_valid_graded_run(self) -> None:
        o = extract.run_outputs(self.LINES, "mux", "baseline", "m", SHA)[0]
        run = parse_run(tomllib.loads(extract.run_skeleton(o)), case())
        self.assertEqual((run.run, run.variant, run.findings), (1, "baseline", ()))

    def test_extract_refuses_a_directory_outside_this_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status, err = run_main(["extract", "pythrust", "--out", tmp])
        self.assertEqual(status, 1)
        self.assertIn("outside this repo", err)


def run_main(argv: list[str]) -> tuple[int, str]:
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        status = main(argv)
    return status, err.getvalue()


if __name__ == "__main__":
    unittest.main()
