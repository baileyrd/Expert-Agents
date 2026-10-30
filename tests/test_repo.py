"""Checks that the experts and the bench cases agree with each other."""

import re
import tomllib
import unittest
from pathlib import Path

from bench.scorer.load import load_case

ROOT = Path(__file__).resolve().parent.parent
EXPERTS = ROOT / "experts"
CASES = ROOT / "bench" / "cases"


def manifest(expert: Path) -> dict:
    with (expert / "expert.toml").open("rb") as f:
        return tomllib.load(f)


def headings(prompt: str) -> set[str]:
    """The prompt's "## " headings, plus "" when text precedes the first one."""
    body = re.sub(r"\A---\n.*?\n---\n", "", prompt, flags=re.S)
    found = set(re.findall(r"^## (.+?)\s*$", body, flags=re.M))
    preamble = body.split("\n## ", 1)[0] if not body.startswith("## ") else ""
    return found | ({""} if preamble.strip() else set())


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


class HeadingsTest(unittest.TestCase):
    def test_frontmatter_is_not_a_preamble(self) -> None:
        self.assertEqual(headings("---\nname: x\n---\n## A\nbody\n"), {"A"})
        self.assertEqual(headings("Intro.\n\n## A\n"), {"", "A"})


if __name__ == "__main__":
    unittest.main()
