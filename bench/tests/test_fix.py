import io
import json
import subprocess
import tempfile
import tomllib
import unittest
import unittest.mock
from contextlib import redirect_stderr
from pathlib import Path

from bench import extract
from bench.fix import grade, sandbox, seed
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


def _record(key, case, sample, files, variant="baseline"):
    return json.dumps({"key": key, "variant": variant, "case": case, "sample": sample,
                       "cli_version": "x", "trace": {"files": files, "final_text": ""}})


class StoreTest(unittest.TestCase):
    JOBS = (extract.Job("k-new", "c", 0),)

    def test_takes_the_run_under_the_current_key_not_the_latest_line(self):
        lines = [_record("k-new", "c", 0, [["a.rs", "new"]]),
                 _record("k-old", "c", 0, [["a.rs", "stale"]])]  # same case and sample, later
        [(job, record)] = extract.current_records(lines, self.JOBS, "baseline")
        self.assertEqual(job.key, "k-new")
        self.assertEqual(record["trace"]["files"], [["a.rs", "new"]])

    def test_refuses_a_missing_run_an_empty_store_or_no_jobs(self):
        for lines, jobs in (([_record("k-old", "c", 0, [])], self.JOBS), ([], self.JOBS),
                            ([_record("k-new", "c", 0, [])], ())):
            with self.assertRaises(ValueError):
                extract.current_records(lines, jobs, "baseline")

    def test_refuses_a_key_that_names_another_case(self):
        with self.assertRaises(ValueError):
            extract.current_records([_record("k-new", "other", 0, [])], self.JOBS, "baseline")


class SandboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cargo, self.rustup = root / "cargo", root / "rustup"
        self.cargo.mkdir()
        self.rustup.mkdir()
        (self.cargo / "credentials.toml").write_text("token = 'secret'")
        self.tree, self.target = root / "tree", root / "target"

    def tearDown(self):
        self.tmp.cleanup()

    def wrap(self):
        return sandbox.wrap(["cargo", "test"], self.tree, self.target, self.cargo,
                            self.rustup, "/usr/bin/bwrap")

    def test_no_network_no_host_env_and_the_command_last(self):
        cmd = self.wrap()
        for flag in ("--unshare-all", "--clearenv", "--die-with-parent"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[-3:], ["--", "cargo", "test"])
        env = {cmd[i + 1]: cmd[i + 2] for i, a in enumerate(cmd) if a == "--setenv"}
        self.assertEqual(env["CARGO_NET_OFFLINE"], "true")
        self.assertEqual(env["HOME"], "/tmp")
        self.assertNotIn("ANTHROPIC_API_KEY", env)

    def test_mounts_only_the_tree_target_toolchain_and_system_dirs(self):
        cmd = self.wrap()
        writable = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--bind"]
        self.assertEqual(writable, [str(self.tree), str(self.target)])
        mounted = {cmd[i + 2] for i, a in enumerate(cmd) if a in ("--bind", "--ro-bind",
                                                                 "--ro-bind-try")}
        allowed = {*sandbox.SYSTEM_DIRS, str(self.cargo), str(self.rustup), str(self.tree),
                   str(self.target), str(self.cargo / "credentials.toml")}
        self.assertLessEqual(mounted, allowed)
        self.assertNotIn("/", mounted)
        self.assertNotIn(str(Path.home()), mounted)

    def test_masks_cargo_credentials(self):
        cmd = self.wrap()
        i = cmd.index(str(self.cargo / "credentials.toml"))
        self.assertEqual(cmd[i - 2:i], ["--ro-bind", "/dev/null"])

    def test_fails_closed_without_bubblewrap(self):
        with unittest.mock.patch("shutil.which", return_value=None):
            with self.assertRaises(ValueError):
                sandbox.bwrap()


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
