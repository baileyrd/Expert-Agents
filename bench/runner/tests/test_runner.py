import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from bench.runner.checkout import CheckoutError, blind_checkout
from bench.runner.config import Boundary, review, rubric
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


BOUNDARY = Boundary(checkout=Path("/work/review/co"), scratch=Path("/work/scratch"),
                    hidden=(Path("/src/expert-agents"), Path("/src/target")))


class ReviewConfigTest(unittest.TestCase):
    def config(self, prior=None, boundary=BOUNDARY):
        c = case(round=2, prior="7-r1") if prior else case()
        return review(c, prior, "PROMPT", "v1", "b" * 40, (boundary,), None)

    def test_no_real_case_s_review_config_holds_its_answer_key(self) -> None:
        from bench.runner.__main__ import CASES
        from bench.scorer.load import load_case
        for case_dir in sorted(CASES.glob("*/*")):
            c = load_case(case_dir)
            text = json.dumps(review(c, None, "PROMPT", "v", "b" * 40, (BOUNDARY,), None))
            for k in c.key:
                for secret in (k.title, k.summary, k.caught_if, k.fix):
                    self.assertNotIn(secret, text, f"{c.id} {k.id}")
            self.assertNotIn("assert", text)
            if c.prior:
                prior = load_case(case_dir.parent / c.prior)
                task = review(c, prior, "P", "v", "b" * 40, (BOUNDARY,), None)["prompts"][0]["raw"]
                self.assertNotIn("/", task, f"{c.id}: promptfoo would read the task as a path")

    def test_the_reviewer_runs_the_prompt_in_the_checkout_without_write_tools(self) -> None:
        provider = self.config()["providers"][0]
        cfg = provider["config"]
        self.assertEqual((provider["label"], cfg["working_dir"], cfg["custom_system_prompt"]),
                         ("v1 run 1", "/work/review/co", "PROMPT"))
        self.assertEqual(self.config()["tests"][0]["vars"]["variant"], "v1")
        self.assertEqual(cfg["custom_allowed_tools"], ["Read", "Grep", "Glob", "Bash"])
        self.assertTrue({"Edit", "Write", "WebFetch", "WebSearch"} <= set(cfg["disallowed_tools"]))
        self.assertIs(cfg["apiKeyRequired"], False)

    def test_hidden_paths_are_denied_to_the_file_tools_and_the_sandbox(self) -> None:
        cfg = self.config()["providers"][0]["config"]
        self.assertIn("Read(//src/expert-agents/**)", cfg["disallowed_tools"])
        self.assertIn("Read(//src/target/**)", cfg["disallowed_tools"])
        sandbox = cfg["sandbox"]
        self.assertEqual((sandbox["enabled"], sandbox["failIfUnavailable"],
                          sandbox["allowUnsandboxedCommands"]), (True, True, False))
        self.assertEqual(sandbox["filesystem"]["denyRead"], ["/src/expert-agents", "/src/target"])
        self.assertEqual(sandbox["filesystem"]["allowWrite"], ["/work/review/co", "/work/scratch"])

    def test_each_run_writes_only_to_its_own_checkout_and_scratch(self) -> None:
        runs = tuple(Boundary(Path(f"/work/review/co-{n}"), Path(f"/work/scratch/{n}"),
                              BOUNDARY.hidden) for n in (1, 2, 3))
        providers = review(case(), None, "P", "v", "b" * 40, runs, None)["providers"]
        writes = [tuple(p["config"]["sandbox"]["filesystem"]["allowWrite"]) for p in providers]
        self.assertEqual(len(providers), 3)
        self.assertEqual(len({w for ws in writes for w in ws}), 6)
        self.assertEqual(len({p["label"] for p in providers}), 3)

    def test_network_is_off_unless_domains_are_allowed(self) -> None:
        off = self.config()["providers"][0]["config"]
        self.assertEqual(off["sandbox"]["network"]["allowedDomains"], [])

    def test_a_narrow_allowed_domain_never_enables_the_web_tools(self) -> None:
        on = self.config(boundary=Boundary(BOUNDARY.checkout, BOUNDARY.scratch, BOUNDARY.hidden,
                                           ("crates.io",)))["providers"][0]["config"]
        self.assertEqual(on["sandbox"]["network"]["allowedDomains"], ["crates.io"])
        self.assertFalse({"WebFetch", "WebSearch"} & set(on["custom_allowed_tools"]))
        self.assertTrue({"WebFetch", "WebSearch"} <= set(on["disallowed_tools"]))

    def test_no_prompt_text_looks_like_a_file_path(self) -> None:
        for prompt in (self.config()["prompts"][0]["raw"], self.config(prior=case())["prompts"][0]["raw"]):
            self.assertNotIn("/", prompt)

    def test_a_re_review_lists_the_prior_findings_and_a_first_review_none(self) -> None:
        self.assertNotIn("Prior findings", self.config()["prompts"][0]["raw"])
        task = self.config(prior=case())["prompts"][0]["raw"]
        self.assertIn("- K1 (P1): Lost token", task)
        self.assertIn("- K2 (blocking): Missing test", task)


class RubricConfigTest(unittest.TestCase):
    def test_each_saved_report_is_graded_per_key_finding(self) -> None:
        c = rubric(case(), [("7-r1-1", "F1 ..."), ("7-r1-2", "F2 ...")], "anthropic:messages:m")
        self.assertEqual(c["providers"], [{"id": "echo"}])
        self.assertEqual([t["vars"]["report"] for t in c["tests"]], ["F1 ...", "F2 ..."])
        asserts = c["tests"][0]["assert"]
        self.assertEqual([a["metric"] for a in asserts], ["K1", "K2"])
        self.assertIn("if the review names the refused retry.", asserts[0]["value"])
        self.assertEqual(c["defaultTest"]["options"]["provider"],
                         {"id": "anthropic:messages:m", "config": {"apiKeyRequired": False}})

    def test_a_non_anthropic_grader_gets_no_login_option(self) -> None:
        c = rubric(case(), [], "openai:x")
        self.assertEqual(c["defaultTest"]["options"]["provider"], {"id": "openai:x", "config": {}})


class ExtractTest(unittest.TestCase):
    RESULTS = {"results": {"results": [
        {"vars": {"case": "mux/7-r1", "prompt_sha": SHA}, "provider": {"id": "p", "label": "v1"},
         "response": {"output": "F1 ..."},
         "latencyMs": 61000, "cost": 1.5, "tokenUsage": {"total": 1200}},
        {"vars": {"case": "mux/7-r1", "prompt_sha": SHA}, "provider": {"id": "p"},
         "response": {"output": None}},
    ]}}

    def test_runs_are_numbered_per_case_with_their_cost(self) -> None:
        first, second = run_outputs(self.RESULTS)
        self.assertEqual((first.run, second.run), (1, 2))
        self.assertEqual(second.variant, "p")
        self.assertIn("61 s, 1200 tokens, $1.50 -->", report_md(first))
        self.assertIn("unknown tokens, unknown", report_md(second))

    def test_a_failed_run_is_kept_apart_from_the_successful_ones(self) -> None:
        failed = {"vars": {"case": "mux/7-r1", "prompt_sha": SHA}, "provider": {"id": "p"},
                  "error": "API error: overloaded"}
        ok, bad = run_outputs({"results": {"results": [self.RESULTS["results"]["results"][0], failed]}})
        self.assertIsNone(ok.error)
        self.assertEqual(bad.error, "API error: overloaded")
        self.assertIn("**Run failed:** API error: overloaded", report_md(bad))

    def test_extract_writes_good_runs_and_exits_1_on_a_failed_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status, _ = extract_into(Path(tmp), [ok_result(), failed_result()])
            written = sorted(p.name for p in (Path(tmp) / "grade" / "out").iterdir())
            rubric_cfg = json.loads((Path(tmp) / "grade" / "mux-1251-r1.rubric.yaml").read_text())
        self.assertEqual(status, 1)
        self.assertEqual(written, ["1251-r1-1-report.md", "1251-r1-1.toml", "1251-r1-2-report.md"])
        self.assertEqual([t["description"] for t in rubric_cfg["tests"]], ["1251-r1-1"])

    def test_extract_refuses_to_overwrite_an_earlier_extract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(extract_into(Path(tmp), [ok_result()])[0], 0)
            out = Path(tmp) / "grade" / "out"
            graded = out / "1251-r1-1.toml"
            graded.write_text(graded.read_text() + "# graded by hand\n")
            before = {p.name: p.read_text() for p in out.iterdir()}
            status, err = extract_into(Path(tmp), [failed_result()])
            after = {p.name: p.read_text() for p in out.iterdir()}
        self.assertEqual(status, 1)
        self.assertIn("already has 1251-r1-1-report.md, 1251-r1-1.toml", err)
        self.assertEqual(before, after)

    def test_the_skeleton_is_a_valid_graded_run(self) -> None:
        import tomllib
        run = parse_run(tomllib.loads(run_skeleton(run_outputs(self.RESULTS)[0])), case())
        self.assertEqual((run.run, run.variant, run.findings), (1, "v1", ()))


def ok_result() -> dict:
    return {"vars": {"case": "mux/1251-r1", "prompt_sha": SHA}, "provider": {"id": "p", "label": "v"},
            "response": {"output": "F1 ..."}}


def failed_result() -> dict:
    return {"vars": {"case": "mux/1251-r1", "prompt_sha": SHA}, "provider": {"id": "p"},
            "error": "boom"}


def extract_into(tmp: Path, results: list[dict], out: Path | None = None) -> tuple[int, str]:
    """Run `extract` on results in a prepared-looking tmp/grade into tmp/grade/out."""
    (tmp / "review").mkdir(exist_ok=True)
    (tmp / "grade").mkdir(exist_ok=True)
    path = tmp / "grade" / "results.json"
    path.write_text(json.dumps({"results": {"results": results}}))
    return run_main(["extract", str(path), "--out", str(out or tmp / "grade" / "out")])


def run_main(argv: list[str]) -> tuple[int, str]:
    from contextlib import redirect_stderr, redirect_stdout
    from bench.runner.__main__ import main
    err = io.StringIO()
    with redirect_stdout(io.StringIO()), redirect_stderr(err):
        status = main(argv)
    return status, err.getvalue()


class PrepareTest(unittest.TestCase):
    def test_review_then_extract_then_review_again_never_reaches_grading_files(self) -> None:
        """The work directory's grade files sit under a path every review config denies."""
        from bench.runner.__main__ import ROOT
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp).resolve()
            src = tmp / "src"
            git(None, "init", "-q", str(src))
            base, head = commit(src, "base"), commit(src, "head")
            case_dir = tmp / "cases" / "mux" / "9-r1"
            case_dir.mkdir(parents=True)
            (case_dir / "pr.toml").write_text(
                f'expert = "mux"\nrepo = "o/r"\npr = 9\ntitle = "t"\nround = 1\n'
                f'base = "{base}"\nhead = "{head}"\n')
            (case_dir / "answer_key.toml").write_text((ROOT / "bench/cases/mux/1251-r1/answer_key.toml").read_text())
            work = tmp / "work"
            self.assertEqual(run_main(["prepare", str(case_dir), "--target", str(src),
                                       "--out", str(work)])[0], 0)
            config = json.loads((work / "mux-9-r1.review.yaml").read_text())
            dirs = {p["config"]["working_dir"] for p in config["providers"]}
            self.assertEqual(len(dirs), 3)  # --repeat's default, one checkout per run
            self.assertTrue(all((Path(d) / ".git").is_dir() for d in dirs))
            (work / "grade" / "results.json").write_text(json.dumps({"results": {"results": [
                {**ok_result(), "vars": {"case": "mux/1251-r1", "prompt_sha": SHA}}]}}))
            self.assertEqual(run_main(["extract", str(work / "grade" / "results.json"),
                                       "--out", str(work / "grade" / "reports")])[0], 0)
            grading = [p for p in (work / "grade").rglob("*") if p.is_file()]
            self.assertTrue(any(p.name.endswith(".rubric.yaml") for p in grading))
            # The same review config, run again now, denies every grading file to both
            # Bash (sandbox) and the file tools.
            cfg = config["providers"][0]["config"]
            denied = [Path(p) for p in cfg["sandbox"]["filesystem"]["denyRead"]]
            for f in grading:
                self.assertTrue(any(d in f.parents for d in denied), str(f))
                self.assertTrue(any(f"Read(/{d}/**)" in cfg["disallowed_tools"]
                                    for d in denied if d in f.parents), str(f))
            self.assertNotIn(work / "grade", Path(cfg["working_dir"]).parents)

    def test_extract_refuses_results_outside_a_prepared_grade_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.json"
            path.write_text(json.dumps({"results": {"results": [ok_result()]}}))
            status, err = run_main(["extract", str(path), "--out", str(Path(tmp) / "out")])
        self.assertEqual(status, 1)
        self.assertIn("not in a prepared work directory", err)

    def test_extract_refuses_graded_runs_where_a_review_could_read_them(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status, err = extract_into(Path(tmp), [ok_result()], out=Path(tmp) / "elsewhere")
        self.assertEqual(status, 1)
        self.assertIn("readable by a later review", err)

    def test_a_work_directory_the_reviewer_may_not_read_is_refused(self) -> None:
        from contextlib import redirect_stderr
        from bench.runner.__main__ import ROOT, main
        err = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, redirect_stderr(err):
            status = main(["prepare", str(ROOT / "bench/cases/mux/1251-r1"), "--target", tmp,
                           "--out", str(ROOT / "reports" / "work")])
        self.assertEqual(status, 1)
        self.assertIn("which the reviewer may not read", err.getvalue())


    def test_a_case_is_named_by_its_directory_or_its_id(self) -> None:
        from bench.runner.__main__ import CASES, _case_dir
        self.assertEqual(_case_dir(Path("mux/1251-r1")), CASES / "mux/1251-r1")
        self.assertEqual(_case_dir(CASES / "mux/1251-r1"), CASES / "mux/1251-r1")

    def test_each_run_is_denied_every_other_run_s_checkout_and_scratch(self) -> None:
        from bench.runner.__main__ import _runs
        runs = _runs(Path("/w"), "c", 3, (Path("/repo"),), ())
        for run in runs:
            others = {d for r in runs if r != run for d in (r.checkout, r.scratch)}
            self.assertEqual(set(run.hidden), {Path("/repo")} | others)
            cfg = review(case(), None, "P", "v", "b" * 40, (run,), None)["providers"][0]["config"]
            for d in others:
                self.assertIn(str(d), cfg["sandbox"]["filesystem"]["denyRead"])
                self.assertIn(f"Read(/{d}/**)", cfg["disallowed_tools"])

    def test_the_work_directory_links_the_pinned_node_modules_once(self) -> None:
        from bench.runner.__main__ import RUNNER, _link_modules
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            _link_modules(out)
            _link_modules(out)  # a second prepare into the same directory is fine
            self.assertEqual((out / "node_modules").resolve(), (RUNNER / "node_modules").resolve())

    def test_a_work_directory_with_other_node_modules_is_refused(self) -> None:
        from bench.runner.__main__ import _link_modules
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "node_modules").mkdir()
            with self.assertRaisesRegex(ValueError, "is not a link"):
                _link_modules(Path(tmp))

class CheckoutTest(unittest.TestCase):
    def test_the_checkout_has_the_head_and_nothing_after_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "src"
            git(None, "init", "-q", str(src))
            base, head, later = (commit(src, n) for n in ("base", "head", "later"))
            dest = Path(tmp) / "co"
            blind_checkout(case(base, head), src, dest)
            self.assertEqual(git(dest, "rev-parse", "HEAD"), head)
            self.assertEqual(git(dest, "remote", "get-url", "origin"), str(dest.resolve()))
            git(dest, "fetch", "-q", "origin", "pull/7/head")
            self.assertEqual(git(dest, "rev-parse", "FETCH_HEAD"), head)
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
