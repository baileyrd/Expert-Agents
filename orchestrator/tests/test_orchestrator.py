"""The generator's skeleton, grounding and checks."""

import contextlib
import io
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

from bench.prompt import sections
from bench.scorer.load import load_case
from bench.scorer.model import Case, KeyFinding

from orchestrator import check, mine, template

ROOT = Path(__file__).resolve().parents[2]
# Ancestry for pure `aliases` tests on a linear history: s1 < s2 < s3.
LINEAR = lambda a, b: a < b  # noqa: E731
EXAMPLES = ROOT / "orchestrator" / "examples"


def case(pr: int = 1246, head: str = "b" * 40, fix: str = "c" * 40) -> Case:
    key = KeyFinding("K1", None, "t", ("f",), fix, "s", "c")
    return Case(f"x/{pr}-r1", "x", "o/r", pr, 1, "a" * 40, head, "t", None, "", (key,))


def skeleton(name: str = "x", axes: int = 3) -> check.Expert:
    return check.Expert(name, tomllib.loads(template.manifest(name, "o/r", axes, "s")),
                        template.subagent(name, axes), template.codex(axes))


def filled(name: str = "x", axes: int = 3) -> check.Expert:
    e = skeleton(name, axes)
    return check.Expert(name, e.manifest, e.subagent.replace(template.TODO, "Filled"),
                        e.codex.replace(template.TODO, "Filled"))


def from_dir(path: Path) -> check.Expert:
    manifest = tomllib.loads((path / "expert.toml").read_text())
    by_kind = {p["kind"]: (path / p["file"]).read_text() for p in manifest["prompt"]}
    return check.Expert(path.name, manifest, by_kind["claude-subagent"], by_kind["codex"])


class TemplateTest(unittest.TestCase):
    def test_skeleton_sections_match_the_manifest(self) -> None:
        e = skeleton(axes=4)
        declared = {s["heading"] for s in e.manifest["section"]}
        self.assertIn("Four axes", declared)
        self.assertEqual(set(sections(e.subagent)), declared)
        self.assertEqual(set(sections(e.codex)), declared)

    def test_axes_outside_two_to_five_are_refused(self) -> None:
        for n in (1, 6):
            with self.assertRaisesRegex(ValueError, "2 to 5"):
                template.axes_heading(n)


class CodexTest(unittest.TestCase):
    def test_codex_is_derived_from_the_subagent_prompt(self) -> None:
        prompt = ("---\nname: x-expert\n---\nYou are the independent reviewer for x, a thing.\n\n"
                  + template.AUTHORITY + "\n" + template.PIN_SUBAGENT
                  + "\n## Findings\n\nFile and line at the head SHA.\n\n" + template.RETURN_SUBAGENT)
        out = template.codex_from(prompt)
        self.assertTrue(out.startswith(template.CODEX_OPENING + "x, a thing.\n\nReview PR <NUMBER>"))
        self.assertIn(template.PIN_CODEX, out)
        self.assertIn("File and line at <HEAD_SHA>.", out)
        self.assertTrue(out.endswith(template.RETURN_CODEX))
        self.assertEqual(check._codex(check.Expert("x", {}, prompt, out)), [])

    def test_a_persona_with_another_opening_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "must open with"):
            template.codex_from("You review x.\n## Return\n")

    def test_each_example_codex_is_derived_from_its_prompt(self) -> None:
        for path in sorted(p for p in EXAMPLES.iterdir() if p.is_dir()):
            self.assertEqual(template.codex_from((path / "prompt.md").read_text()),
                             (path / "codex.md").read_text(), path.name)


class CheckTest(unittest.TestCase):
    def test_a_skeleton_is_refused_for_its_todos(self) -> None:
        found = check.problems(skeleton(), [])
        self.assertEqual(found, [f"prompt.md still has {template.TODO} markers",
                                 f"codex.md still has {template.TODO} markers"])

    def test_a_filled_skeleton_passes(self) -> None:
        self.assertEqual(check.problems(filled(), [case()]), [])

    def test_a_held_out_pr_number_is_a_leak(self) -> None:
        e = filled()
        leaky = check.Expert(e.dir_name, e.manifest, e.subagent + "\nAs in #1246.\n", e.codex)
        self.assertEqual(check.problems(leaky, [case()]),
                         ["prompt.md names held-out PR 1246 (x/1246-r1)"])

    def test_a_held_out_fix_sha_is_a_leak(self) -> None:
        e = filled()
        leaky = check.Expert(e.dir_name, e.manifest, e.subagent, e.codex + "\nSee ccccccc.\n")
        self.assertEqual(check.problems(leaky, [case()]),
                         ["codex.md names held-out commit cccccccccc"])

    def test_any_held_out_commit_is_a_leak_not_only_endpoints(self) -> None:
        e = filled()
        mid = "d" * 40
        leaky = check.Expert(e.dir_name, e.manifest, e.subagent + f"\nSee {mid}.\n", e.codex)
        self.assertEqual(check.problems(leaky, [case()]), [])
        self.assertEqual(check.problems(leaky, [case()], frozenset({mid})),
                         ["prompt.md names held-out commit dddddddddd"])

    def test_a_longer_number_is_not_the_pr(self) -> None:
        e = filled()
        other = check.Expert(e.dir_name, e.manifest, e.subagent + "\n#12460 and PR 1246a\n", e.codex)
        self.assertEqual(check.problems(other, [case()]), [])

    def test_a_wrong_subagent_name_and_missing_placeholder_are_problems(self) -> None:
        e = filled()
        bad = check.Expert(e.dir_name, e.manifest, e.subagent.replace("x-expert", "y-expert"),
                           e.codex.replace("<PRIOR_FINDINGS>", ""))
        self.assertEqual(check.problems(bad, []), [
            "prompt.md name 'y-expert'; want 'x-expert'",
            "codex.md lacks placeholder <PRIOR_FINDINGS>"])

    def test_a_dropped_section_is_a_problem(self) -> None:
        e = filled()
        text = e.subagent.replace("## Checks\n", "")
        self.assertEqual(len(check.problems(check.Expert("x", e.manifest, text, e.codex), [])), 1)

    def test_measured_needs_three_prs(self) -> None:
        self.assertEqual(check.notes([case(1), case(2), case(3)]), [])
        self.assertEqual(check.notes([case(1), case(1)]),
                         ["1 past PR(s) in bench/cases; measured needs 3"])


class MineTest(unittest.TestCase):
    LOG = ("\x1eaaa\x1fcrate: answer the review of 123\x1f\x1f\nsrc/a.rs\nsrc/b.rs\n"
           "\x1ebbb\x1fcrate: add a feature\x1fBody text.\x1f\nsrc/c.rs\n"
           "\x1eccc\x1fcrate: bound the queue\x1fFixes P1 from the review.\x1f\nsrc/d.rs\n")

    def test_parse_log_reads_subject_body_and_files(self) -> None:
        commits = mine.parse_log(self.LOG)
        self.assertEqual([c.sha for c in commits], ["aaa", "bbb", "ccc"])
        self.assertEqual(commits[0].files, ("src/a.rs", "src/b.rs"))
        self.assertEqual(commits[2].body, "Fixes P1 from the review.")

    def test_grounding_keeps_review_fixes_and_drops_held_out(self) -> None:
        md = mine.grounding_md(mine.parse_log(self.LOG), frozenset({"ccc"}), ("src",))
        self.assertIn("| aaa | crate: answer the review of 123 |", md)
        self.assertNotIn("bbb", md)
        self.assertNotIn("ccc |", md)
        self.assertIn("1 commits answer a review; 1 held-out commits removed.", md)

    def test_held_out_covers_the_pr_up_to_each_fix(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            run = lambda *a: subprocess.run(["git", "-C", d, *a], check=True,  # noqa: E731
                                            capture_output=True, text=True).stdout.strip()
            run("init", "-q")
            shas = []
            for i in range(4):
                run("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                    "--allow-empty", "--no-gpg-sign", "-m", f"c{i}")
                shas.append(run("rev-parse", "HEAD"))
            key = KeyFinding("K1", None, "t", (), shas[2], "s", "c")
            pr = Case("x/1-r1", "x", "o/r", 1, 1, shas[0], shas[1], "t", None, "", (key,))
            held = mine.held_out(repo, [pr])
        self.assertEqual(held, frozenset(shas[1:3]))


    def test_a_defect_named_without_fix_is_a_review_fix(self) -> None:
        for subject in ("journal: events were silently dropped on overflow",
                        "store: lost writes when the WAL rotates", "pool: deadlock on shutdown"):
            self.assertTrue(mine.answers_review(mine.Commit("s", subject, "", ())), subject)

    def test_mechanical_upkeep_is_not_a_review_fix_even_when_it_says_fix(self) -> None:
        for subject in ("fix clippy warnings", "cargo fmt after review", "fix typos in docs",
                        "chore: fix CI cache", "style(core): fix import order",
                        "plan: P0 tasks for the next phase"):
            self.assertFalse(mine.answers_review(mine.Commit("s", subject, "Fixes P1.", ())),
                             subject)

    def test_a_feature_whose_body_mentions_a_bug_is_not_a_review_fix(self) -> None:
        for subject in ("nexus: add a cache", "Phase 4: ablation and a regression gate"):
            commit = mine.Commit("s", subject, "Avoids the bug class where reads race.", ())
            self.assertFalse(mine.answers_review(commit), subject)

    def test_parse_renames_reads_raw_paths_per_commit(self) -> None:
        text = ("commit s2\0\nR100\0old/a.rs\0new/a.rs\0M\0R1.rs\0"
                "commit s1\0\nR087\0x\ty.rs\n\0\"y\".rs\0")
        self.assertEqual(mine.parse_renames(text),
                         [mine.Rename("s2", "old/a.rs", "new/a.rs"),
                          mine.Rename("s1", "x\ty.rs\n", '"y".rs')])

    def test_aliases_follow_a_file_moved_twice(self) -> None:
        second = mine.Rename("s2", "mid/nexus/a.rs", "crates/nexus/a.rs")
        first = mine.Rename("s1", "nexus/a.rs", "mid/nexus/a.rs")
        other = mine.Rename("s3", "other/b.rs", "elsewhere/b.rs")
        for scope in ("crates/nexus", "./crates/nexus/"):
            self.assertEqual(mine.aliases((scope,), [second, first, other], LINEAR),
                             (second, first))

    def test_aliases_skip_a_later_rename_into_a_freed_old_name(self) -> None:
        reuse = mine.Rename("s3", "x/a.rs", "nexus/a.rs")
        move = mine.Rename("s2", "nexus/a.rs", "crates/a.rs")
        older = mine.Rename("s1", "lib/a.rs", "nexus/a.rs")
        self.assertEqual(mine.aliases(("crates",), [reuse, move, older], LINEAR), (move, older))

    def test_aliases_skip_a_rename_on_a_parallel_branch_listed_after_the_move(self) -> None:
        move = mine.Rename("s2", "nexus/a.rs", "crates/a.rs")
        parallel = mine.Rename("p1", "x/a.rs", "nexus/a.rs")
        self.assertEqual(mine.aliases(("crates",), [move, parallel], lambda a, b: False), (move,))

    def test_aliases_need_a_whole_path_component(self) -> None:
        renames = [mine.Rename("s", "old.rs", "src/ab.rs")]
        self.assertEqual(mine.aliases(("src/a",), renames, LINEAR), ())

    def test_review_fixes_skip_a_merged_parallel_rename_into_the_old_name(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            run = lambda *a: subprocess.run(["git", "-C", d, *a], check=True,  # noqa: E731
                                            capture_output=True, text=True).stdout.strip()
            commit = lambda msg: run("-c", "user.name=t", "-c", "user.email=t@t",  # noqa: E731
                                     "commit", "-q", "--no-gpg-sign", "-am", msg)
            run("init", "-q", "-b", "main")
            for name, text in (("old", "the real a\n"), ("x", "an unrelated a\n")):
                (repo / name).mkdir()
                (repo / name / "a.rs").write_text(text * 20)
            run("add", ".")
            commit("add both")
            run("checkout", "-q", "-b", "side")
            (repo / "x" / "a.rs").write_text("fixed\n" + "an unrelated a\n" * 19)
            commit("x: fix a crash in the unrelated a")
            run("rm", "-q", "old/a.rs")
            commit("drop old/a")
            (repo / "old").mkdir()
            run("mv", "x/a.rs", "old/a.rs")
            commit("move x/a into old/a")
            run("checkout", "-q", "main")
            run("mv", "old", "new")
            commit("move old to new")
            # Merge main into side, keeping main's tree, so the move is listed first.
            run("checkout", "-q", "side")
            run("merge", "-q", "--no-commit", "-s", "ours", "main")
            run("read-tree", "-u", "--reset", "main")
            commit("merge main")
            subjects = [c.subject for c in mine.review_fixes(repo, ("new",))]
        self.assertIn("move old to new", subjects)
        self.assertNotIn("x: fix a crash in the unrelated a", subjects)

    def test_merge_logs_unions_files_in_the_given_order(self) -> None:
        a, b = mine.Commit("a", "s", "", ("new/x",)), mine.Commit("b", "t", "", ("new/y",))
        old_a = mine.Commit("a", "s", "", ("old/x",))
        merged = mine.merge_logs([[a, b], [old_a]], ["b", "a"])
        self.assertEqual([(c.sha, c.files) for c in merged],
                         [("b", ("new/y",)), ("a", ("new/x", "old/x"))])

    def test_review_fixes_find_a_fix_made_before_the_scope_moved(self) -> None:
        for name, scope in (("a.rs", "crates"), ("é [1].rs", "./crates/")):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as d:
                subjects = self._moved_twice(d, name, scope)
            self.assertIn("nexus: queue silently dropped events", subjects)
            self.assertNotIn("nexus: fix a bug in the unrelated file", subjects)
            self.assertNotIn("x: fix a crash in b", subjects)

    @staticmethod
    def _moved_twice(d: str, name: str, scope: str) -> list[str]:
        """Subjects under `scope` after nexus/ -> mid/ -> crates/, then two reuses of nexus/:
        a new file created there, and an unrelated x/b.rs renamed there."""
        repo = Path(d)
        run = lambda *a: subprocess.run(["git", "-C", d, *a], check=True,  # noqa: E731
                                        capture_output=True, text=True).stdout.strip()
        commit = lambda msg: run("-c", "user.name=t", "-c", "user.email=t@t",  # noqa: E731
                                 "commit", "-q", "--no-gpg-sign", "-am", msg)
        run("init", "-q")
        (repo / "nexus").mkdir()
        (repo / "nexus" / name).write_text("fn a() {}\n" * 20)
        run("add", ".")
        commit("nexus: add a")
        (repo / "nexus" / name).write_text("fn a() { b() }\n" + "fn a() {}\n" * 19)
        commit("nexus: queue silently dropped events")
        for src, dst in (("nexus", "mid"), ("mid", "crates")):
            run("mv", src, dst)
            commit(f"move {src} to {dst}")
        (repo / "nexus").mkdir()
        (repo / "nexus" / name).write_text("something else\n")
        run("add", ".")
        commit("nexus: unrelated file reusing the old name")
        (repo / "nexus" / name).write_text("something else, fixed\n")
        commit("nexus: fix a bug in the unrelated file")
        (repo / "x").mkdir()
        (repo / "x" / "b.rs").write_text("another thing\n" * 20)
        run("add", ".")
        commit("x: add b")
        (repo / "x" / "b.rs").write_text("another thing, fixed\n" + "another thing\n" * 19)
        commit("x: fix a crash in b")
        run("rm", "-q", "-r", "nexus")
        commit("drop the replacement")
        (repo / "nexus").mkdir()
        run("mv", "x/b.rs", f"nexus/{name}")
        commit("move b into the freed old name")
        return [c.subject for c in mine.review_fixes(repo, (scope,))]

class CliTest(unittest.TestCase):
    def test_a_named_cases_dir_that_does_not_exist_is_refused(self) -> None:
        from orchestrator.__main__ import main
        for argv in (["check", str(EXAMPLES / "conhost"), "--cases", "bench/cases/nope"],
                     ["mine", "--target", str(ROOT), "--cases", "bench/cases/nope", "--", "x"]):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(argv), 1)
            self.assertIn("no cases directory", err.getvalue())

    def test_check_with_cases_requires_a_target(self) -> None:
        from orchestrator.__main__ import main
        with contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["check", str(EXAMPLES / "conhost")]), 1)
        self.assertIn("--target <clone> is required", err.getvalue())


class ExampleTest(unittest.TestCase):
    def test_every_worked_example_is_ready_to_bench_on_its_cases(self) -> None:
        examples = sorted(p for p in EXAMPLES.iterdir() if p.is_dir())
        self.assertTrue(examples)
        for path in examples:
            cases = [load_case(d) for d in sorted((ROOT / "bench" / "cases" / path.name).iterdir())]
            self.assertEqual(check.problems(from_dir(path), cases), [], path.name)


if __name__ == "__main__":
    unittest.main()
