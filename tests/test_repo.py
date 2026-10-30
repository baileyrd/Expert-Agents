"""Checks that the experts and the bench cases agree with each other."""

import tomllib
import unittest
from pathlib import Path

from bench.prompt import body, sections, without
from bench.scorer.load import load_case, load_run

ROOT = Path(__file__).resolve().parent.parent
EXPERTS = ROOT / "experts"
CASES = ROOT / "bench" / "cases"
REPORTS = ROOT / "reports"


def manifest(expert: Path) -> dict:
    with (expert / "expert.toml").open("rb") as f:
        return tomllib.load(f)


def headings(prompt: str) -> set[str]:
    return set(sections(prompt))


class ExpertsTest(unittest.TestCase):
    def test_each_expert_folder_names_itself_and_its_prompts_exist(self) -> None:
        for expert in sorted(p for p in EXPERTS.iterdir() if p.is_dir()):
            m = manifest(expert)
            self.assertEqual(m["name"], expert.name)
            for prompt in m["prompt"]:
                self.assertTrue((expert / prompt["file"]).is_file(), f"{expert.name}: {prompt['file']}")

    def test_every_declared_section_is_a_heading_in_every_prompt(self) -> None:
        for expert in sorted(p for p in EXPERTS.iterdir() if p.is_dir()):
            m = manifest(expert)
            declared = {s["heading"] for s in m.get("section", [])}
            for prompt in m["prompt"]:
                if not declared:
                    continue
                found = headings((expert / prompt["file"]).read_text())
                self.assertEqual(declared, found, f"{expert.name}/{prompt['file']}")

    def test_no_two_prompts_sync_to_the_same_target(self) -> None:
        targets = [(p["target_repo"], p["target_path"])
                   for e in EXPERTS.iterdir() if e.is_dir() for p in manifest(e)["prompt"]]
        self.assertEqual(len(targets), len(set(targets)))

    def test_a_claude_subagent_prompt_has_frontmatter(self) -> None:
        for expert in sorted(p for p in EXPERTS.iterdir() if p.is_dir()):
            for prompt in manifest(expert)["prompt"]:
                if prompt["kind"] == "claude-subagent":
                    text = (expert / prompt["file"]).read_text()
                    self.assertRegex(text, r"\A---\nname: [\w-]+\n", f"{expert.name}")


class CasesTest(unittest.TestCase):
    def test_every_case_loads_and_belongs_to_an_expert(self) -> None:
        for case_dir in sorted(CASES.glob("*/*")):
            case = load_case(case_dir)
            self.assertTrue((EXPERTS / case.expert / "expert.toml").is_file(), case.id)

    def test_a_prior_round_is_an_earlier_case_of_the_same_pr(self) -> None:
        for case_dir in sorted(CASES.glob("*/*")):
            case = load_case(case_dir)
            if case.prior is None:
                continue
            prior = load_case(case_dir.parent / case.prior)
            self.assertEqual((prior.repo, prior.pr), (case.repo, case.pr), case.id)
            self.assertLess(prior.round, case.round, case.id)


class RunsTest(unittest.TestCase):
    def test_every_graded_run_loads_against_its_case(self) -> None:
        runs = sorted(REPORTS.glob("*/*/*.toml"))
        self.assertTrue(runs)
        for path in runs:
            with path.open("rb") as f:
                case_id = tomllib.load(f)["case"]
            load_run(path, load_case(CASES / case_id))


class PromptTest(unittest.TestCase):
    def test_frontmatter_is_not_a_preamble(self) -> None:
        self.assertEqual(headings("---\nname: x\n---\n## A\nbody\n"), {"A"})
        self.assertEqual(headings("Intro.\n\n## A\n"), {"", "A"})

    def test_without_drops_only_the_named_sections(self) -> None:
        prompt = "---\nname: x\n---\nIntro.\n## A\na\n## B\nb\n"
        self.assertEqual(without(prompt, frozenset()), "Intro.\n## A\na\n## B\nb\n")
        self.assertEqual(without(prompt, frozenset({"A"})), "Intro.\n## B\nb\n")
        self.assertEqual(without(prompt, frozenset({""})), "## A\na\n## B\nb\n")

    def test_without_refuses_an_unknown_section(self) -> None:
        with self.assertRaisesRegex(ValueError, "no such"):
            without("## A\n", frozenset({"Z"}))

    def test_dropping_nothing_keeps_every_real_prompt_whole(self) -> None:
        for path in EXPERTS.glob("*/*.md"):
            text = path.read_text()
            self.assertEqual(without(text, frozenset()), body(text), str(path))


if __name__ == "__main__":
    unittest.main()
