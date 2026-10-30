import subprocess
import tempfile
import unittest
from pathlib import Path

from bench.runner.checkout import CheckoutError, blind_checkout
from bench.runner.config import build
from bench.runner.extract import report_md, run_outputs, run_skeleton
from bench.scorer.model import parse_case, parse_run

SHA = "a" * 40


def case(base: str = SHA, head: str = SHA, prior: str | None = None, round: int = 1):
    pr = {"expert": "mux", "repo": "o/r", "pr": 7, "round": round, "base": base, "head": head,
          "title": "t", **({"prior": prior} if prior else {})}
    key = {"review": "r", "finding": [
        {"id": "K1", "severity": "P1", "title": "Lost token", "files": ["f.rs"], "fix": SHA,
         "summary": "The retry is refused.", "caught_if": "Names the refused retry."},
        {"id": "K2", "severity": "ungraded", "title": "Missing test", "files": ["f.rs"],
         "fix": SHA, "summary": "s", "caught_if": "Names it."},
    ]}
    return parse_case(f"mux/7-r{round}", pr, key)


class ConfigTest(unittest.TestCase):
    def config(self, prior=None):
        return build(case(round=2, prior="7-r1") if prior else case(), prior, "PROMPT", "v1",
                     "b" * 40, Path("/work/co"), "grader:x", None)

    def test_the_reviewer_runs_the_prompt_in_the_checkout_with_review_tools(self) -> None:
        provider = self.config()["providers"][0]
        self.assertEqual(provider["label"], "v1")
        self.assertEqual(provider["config"]["working_dir"], "/work/co")
        self.assertEqual(provider["config"]["custom_system_prompt"], "PROMPT")
        self.assertNotIn("Write", provider["config"]["custom_allowed_tools"])
        self.assertNotIn("model", provider["config"])
        self.assertIs(provider["config"]["apiKeyRequired"], False)

    def test_an_anthropic_grader_uses_the_claude_code_login(self) -> None:
        c = build(case(), None, "P", "v", "b" * 40, Path("/co"), "anthropic:messages:m", None)
        grader = c["defaultTest"]["options"]["provider"]
        self.assertEqual(grader, {"id": "anthropic:messages:m", "config": {"apiKeyRequired": False}})

    def test_each_key_finding_is_a_named_rubric_and_the_grader_is_explicit(self) -> None:
        c = self.config()
        asserts = c["tests"][0]["assert"]
        self.assertEqual([a["metric"] for a in asserts], ["K1", "K2"])
        self.assertIn("if the review names the refused retry.", asserts[0]["value"])
        self.assertEqual(c["defaultTest"]["options"]["provider"], {"id": "grader:x", "config": {}})
        self.assertEqual(c["tests"][0]["vars"]["prompt_sha"], "b" * 40)

    def test_a_re_review_lists_the_prior_findings_and_a_first_review_none(self) -> None:
        self.assertNotIn("Prior findings", self.config()["prompts"][0])
        task = self.config(prior=case())["prompts"][0]
        self.assertIn("- K1 (P1): Lost token", task)
        self.assertIn("- K2 (blocking): Missing test", task)


class ExtractTest(unittest.TestCase):
    RESULTS = {"results": {"results": [
        {"vars": {"case": "mux/7-r1", "prompt_sha": SHA}, "provider": {"id": "p", "label": "v1"},
         "response": {"output": "F1 ..."}, "namedScores": {"K1": 1, "K2": 0.5},
         "latencyMs": 61000, "cost": 1.5, "tokenUsage": {"total": 1200}},
        {"vars": {"case": "mux/7-r1", "prompt_sha": SHA}, "provider": {"id": "p"},
         "response": {"output": None}},
    ]}}

    def test_runs_are_numbered_per_case_with_their_cost(self) -> None:
        first, second = run_outputs(self.RESULTS)
        self.assertEqual((first.run, second.run), (1, 2))
        self.assertEqual(second.variant, "p")
        self.assertIn("61 s, 1200 tokens, $1.50; rubric recall: K1 1, K2 0.5", report_md(first))
        self.assertIn("unknown tokens, unknown", report_md(second))

    def test_the_skeleton_is_a_valid_graded_run(self) -> None:
        import tomllib
        run = parse_run(tomllib.loads(run_skeleton(run_outputs(self.RESULTS)[0])), case())
        self.assertEqual((run.run, run.variant, run.findings), (1, "v1", ()))


class CheckoutTest(unittest.TestCase):
    def test_the_checkout_has_the_head_and_nothing_after_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            git(None, "init", "-q", str(src))
            base, head, later = (commit(src, n) for n in ("base", "head", "later"))
            dest = Path(tmp) / "co"
            blind_checkout(case(base, head), src, dest)
            self.assertEqual(git(dest, "rev-parse", "HEAD"), head)
            self.assertEqual(git(dest, "remote"), "")
            missing = subprocess.run(["git", "cat-file", "-e", later], cwd=dest, check=False)
            self.assertNotEqual(missing.returncode, 0)
            with self.assertRaisesRegex(CheckoutError, "already exists"):
                blind_checkout(case(base, head), src, dest)

    def test_a_commit_the_clone_lacks_is_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            git(None, "init", "-q", str(src))
            base = commit(src, "base")
            with self.assertRaisesRegex(CheckoutError, "fetch"):
                blind_checkout(case(base, "c" * 40), src, Path(tmp) / "co")


def git(cwd: Path | None, *args: str) -> str:
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin"}
    out = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=cwd, env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def commit(repo: Path, name: str) -> str:
    (repo / name).write_text(name)
    git(repo, "add", name)
    git(repo, "commit", "-q", "-m", name)
    return git(repo, "rev-parse", "HEAD")


if __name__ == "__main__":
    unittest.main()
