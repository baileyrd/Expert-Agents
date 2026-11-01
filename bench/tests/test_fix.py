import io
import json
import subprocess
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from bench.fix import grade, seed
from bench.fix.__main__ import CASES, cases, developer_of, main
from bench.fix.model import parse_case, settings, suite_toml, task_input

SHA = "a" * 40


def raw(**over):
    return {"developer": "d", "repo": "o/r", "fix": SHA, "base": "b" * 40,
            "held_back": ["crates/x/tests/t.rs"], "test": ["cargo", "test"],
            "task": "Bug: it hangs.", **over}


class ModelTest(unittest.TestCase):
    def test_parses_a_case(self):
        c = parse_case("d/c", raw())
        self.assertEqual(c.held_back, ("crates/x/tests/t.rs",))
        self.assertIn("Bug: it hangs.", task_input(c))
        self.assertIn("b" * 40, task_input(c))
        self.assertNotIn(SHA, task_input(c))  # the fix commit is never shown

    def test_rejects_bad_cases(self):
        for bad in (raw(fix="abc"), raw(held_back=["src/lib.rs"]), raw(test=[]), raw(task=" "),
                    raw(extra=1)):
            with self.assertRaises(ValueError):
                parse_case("d/c", bad)

    def test_suite_starts_at_the_base_and_hides_the_clone(self):
        text = suite_toml("d", [parse_case("d/c", raw())], "m", ("index.crates.io",))
        data = tomllib.loads(text)
        self.assertEqual(data["case"][0]["checkout"], {"from": "${TARGET}", "head": "b" * 40})
        self.assertNotIn(SHA, text)
        rules = json.loads(data["settings"])
        self.assertIn("Write(/${TARGET}/**)", rules["permissions"]["deny"])
        self.assertIn("Agent", rules["permissions"]["deny"])
        self.assertEqual(rules["sandbox"]["network"]["allowedDomains"], ["index.crates.io"])

    def test_settings_never_open_github(self):
        self.assertNotIn("github", settings(()))

    def test_a_seeded_case_carries_its_patch_and_hidden_tests(self):
        c = parse_case("d/c", raw(seed="seed.patch"), "PATCH", {"crates/x/tests/t.rs": "T"})
        self.assertTrue(c.seeded)
        self.assertEqual(c.hidden, (("crates/x/tests/t.rs", "T"),))
        self.assertEqual(grade.held_back(c, Path("/nonexistent")), {"crates/x/tests/t.rs": "T"})
        self.assertNotIn("PATCH", task_input(c))
        self.assertFalse(parse_case("d/c", raw()).seeded)

    def test_a_seeded_case_needs_its_patch_and_every_hidden_test(self):
        for args in ((raw(seed="seed.patch"), "", {}),
                     (raw(seed="seed.patch"), "PATCH", {}),
                     (raw(), "PATCH", {"crates/x/tests/t.rs": "T"})):
            with self.assertRaises(ValueError):
                parse_case("d/c", *args)

    def test_a_case_set_names_one_developer(self):
        self.assertEqual(developer_of([parse_case("s/c", raw())]), "d")
        for found in ([], [parse_case("s/a", raw()), parse_case("s/b", raw(developer="e"))],
                      [parse_case("s/c", raw(developer=""))]):
            with self.assertRaises(ValueError):
                developer_of(found)


class SeedTest(unittest.TestCase):
    PATCH = """diff --git a/src/lib.rs b/src/lib.rs
--- a/src/lib.rs
+++ b/src/lib.rs
@@ -1 +1 @@
-fn ok() {}
+fn bug() {}
"""

    def test_plant_builds_the_same_parentless_commit_every_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fix = _repo_with(repo, {"src/lib.rs": "fn ok() {}\n"})
            c = parse_case("s/c", raw(fix=fix, seed="seed.patch"), self.PATCH,
                           {"crates/x/tests/t.rs": "T"})
            first, second = seed.plant(c, repo), seed.plant(c, repo)
            self.assertEqual(first, second)
            self.assertNotIn("parent", _git(repo, "cat-file", "-p", first))
            self.assertEqual(_git(repo, "show", f"{first}:src/lib.rs"), "fn bug() {}\n")
            self.assertEqual(_git(repo, "rev-parse", seed.ref(c)).strip(), first)

    def test_plant_fails_when_the_patch_does_not_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            fix = _repo_with(repo, {"src/lib.rs": "fn other() {}\n"})
            c = parse_case("s/c", raw(fix=fix, seed="seed.patch"), self.PATCH,
                           {"crates/x/tests/t.rs": "T"})
            with self.assertRaises(ValueError):
                seed.plant(c, repo)


def _repo_with(repo: Path, files: dict[str, str]) -> str:
    _git(repo, "init", "-q")
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    _git(repo, "add", ".")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-gpg-sign",
         "-m", "init")
    return _git(repo, "rev-parse", "HEAD").strip()


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout


class GradeTest(unittest.TestCase):
    def test_source_files_skip_build_output_and_report_lost_text(self):
        kept, lost = grade.source_files([("target/debug/x", None), ("a/target/b.rs", "x"),
                                         ("src/lib.rs", "fn f() {}"), ("big.rs", None)])
        self.assertEqual(kept, {"src/lib.rs": "fn f() {}"})
        self.assertEqual(lost, ["big.rs"])

    def test_own_tests_are_files_under_a_tests_directory(self):
        files = {"crates/x/tests/t.rs": "", "crates/x/src/tests.rs": "", "tests/a.rs": ""}
        self.assertEqual(sorted(grade.own_tests(files)), ["crates/x/tests/t.rs", "tests/a.rs"])

    def test_solved_needs_fixed_and_green(self):
        self.assertTrue(grade.Grade("d/c", 1, fixed=True, suite_green=True).solved)
        self.assertFalse(grade.Grade("d/c", 1, fixed=True, suite_green=False).solved)


class CommittedTest(unittest.TestCase):
    def test_committed_cases_parse_and_suites_are_current(self):
        for developer in (d.name for d in CASES.iterdir() if d.is_dir()):
            self.assertTrue(cases(developer))
            with redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(["suite", developer, "--check"]), 0, err.getvalue())


if __name__ == "__main__":
    unittest.main()
