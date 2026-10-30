"""The generator's skeleton, grounding and checks."""

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
                         ["codex.md names held-out commit cccccccccc (x/1246-r1)"])

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


class ExampleTest(unittest.TestCase):
    def test_every_worked_example_is_ready_to_bench_on_its_cases(self) -> None:
        examples = sorted(p for p in EXAMPLES.iterdir() if p.is_dir())
        self.assertTrue(examples)
        for path in examples:
            cases = [load_case(d) for d in sorted((ROOT / "bench" / "cases" / path.name).iterdir())]
            self.assertEqual(check.problems(from_dir(path), cases), [], path.name)


if __name__ == "__main__":
    unittest.main()
