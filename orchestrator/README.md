# Orchestrator

How a new expert is generated, then grounded and measured. The
[expert-generator skill](../.claude/skills/expert-generator/SKILL.md) runs the whole
procedure in Claude Code; the commands below are its deterministic parts.

```
python3 -m orchestrator new <name> --target-repo OWNER/REPO --axes N   # skeleton
python3 -m orchestrator mine --target <clone> --cases bench/cases/<name> -- <paths>
python3 -m orchestrator codex experts/<name>    # codex.md from prompt.md
python3 -m orchestrator check experts/<name>    # TODOs, sections, held-out leaks
```

[examples/conhost/](examples/conhost/) is a worked example: the conhost reviewer
regenerated from its interview answer, grounded with conhost PR 1246 held out, so it
can be benched against the hand-written prompt on the same cases.

## Interview

In the target project, ask:

1. "Describe this project in five words."
2. "Describe the expert reviewer you'd look for": about five profiles, and the one to
   prioritise.

The answers give the persona and the axes the reviewer checks.

## Ground

A new expert starts from its domain's fix commits and review threads, not from general
expertise: mine them for the defect classes reviews actually caught, and for primary
sources the reviewer can cite (the reference implementation, its tests, client source).
Hold the PRs chosen as answer keys out of the prompt's examples.

## Loop

1. **Key.** Pick a past PR in the domain that had review rounds and build its case
   ([bench/README.md](../bench/README.md)). Done when every key finding cites the review
   and the fix commit.
2. **Trial.** Run the frozen prompt blind on the case, N times.
3. **Score.** Grade every finding; recall, precision and severity from the scorer.
4. **Improve.** Change a prompt only for a gap a trial showed: a missed key finding, a
   false-positive pattern, or an instruction the reviewer reported as unclear. Re-run
   every case for that expert, so a fix for one does not regress another.
5. **Report real bugs.** Run a real finding on current `main` first, search for
   duplicates, and file it in the target repo's issue format. Anything a user could be
   attacked through goes to the owner to choose a public issue or a private advisory.
6. **Land.** Prompt, cases and report land here by PR; then [sync](../sync/README.md).

An expert is measured when it has three or more cases with recall and precision
recorded, and in use when DottiusMaximus has run it on a live PR.

## Roles

Reviewer first, then tester (writes tests aimed at known defect classes, scored by
failing on the buggy SHA and passing on the fix), then designer, builder and idea
intake.
