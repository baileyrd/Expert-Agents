# Release Notes

expert-generator lives at
[github.com/baileyrd/Expert-Agents](https://github.com/baileyrd/Expert-Agents/tree/main/.claude/skills/expert-generator) —
this log tracks commits against `main`.

---

## v1.2.0 — Gaps found by the first eval runs
**2026-10-30**

Six sandboxed runs (three prompts, v1.1.0 against v1.0.0) agreed on these.

- **Fixed:** step 4 commits the expert before `python3 -m unittest`; the runner's tests
  refuse uncommitted changes under `experts/`, so the old order always failed once.
- **Added:** held-out findings must not be restated as general rules, which `check`
  cannot see; reread the prompt against each answer key.
- **Added:** a fallback when review threads cannot be read (cite the quoting fix commit,
  severity ungraded).
- **Added:** off-machine handoff runs `prepare` first, opens a draft PR, and commits a
  variant's files where the bench machine can get them.

## v1.1.0 — Brought up to the skill_pack authoring standard
**2026-10-30**

- **Changed:** `description` is a `>-` block scalar in the third person, states the
  mechanism (trait-to-axis Profile, mined review fixes, held-out cases, variant gate)
  and the trigger phrasings; added `metadata.version` and `compatibility`. The version
  sits under `metadata` because a top-level `version` key fails a claude.ai upload,
  the Skills API and `package_skill.py`; it is a skill_pack-only convention.
- **Added:** a copyable step checklist; the reasons behind repo-grounded defect classes
  and held-out PRs; a fix-and-rerun loop on `check` before benching.
- **Added:** regenerating an expert needs its interview answer: use `interview.md` or
  ask, never infer one from the current prompt.
- **Fixed:** the step 5 `prepare` command passes the now-required `--model`, the same
  for baseline and variant.
- **Added:** step 8, a read-only wrap-up retro via `skill-retro`, grounded in this
  skill's own steps, with a fallback to a `## Retro` section when `skill-retro` (from
  baileyrd/skill_pack) is not installed. Applying findings stays a separate PR.
- **Added:** `evals/evals.json` with three prompts: a new expert (crdt), a variant
  (mux), and a casual ask (rusty_tick).

## v1.0.0 — Initial release
**2026-10-30**

- **Added:** the skill with `python3 -m orchestrator new/mine/codex/check`
  ([#3](https://github.com/baileyrd/Expert-Agents/pull/3)); variant comparison on the
  same cases, copying `expert.toml` with the variant.
