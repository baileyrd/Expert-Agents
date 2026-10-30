# Release Notes

expert-generator lives at
[github.com/baileyrd/Expert-Agents](https://github.com/baileyrd/Expert-Agents/tree/main/.claude/skills/expert-generator) —
this log tracks commits against `main`.

---

## v1.1.0 — Brought up to the skill_pack authoring standard
**2026-10-30**

- **Changed:** `description` is a `>-` block scalar in the third person, states the
  mechanism (trait-to-axis Profile, mined review fixes, held-out cases, variant gate)
  and the trigger phrasings; added `metadata.version` and `compatibility`. The version
  sits under `metadata` because a top-level `version` key fails a claude.ai upload,
  the Skills API and `package_skill.py`; it is a skill_pack-only convention.
- **Added:** a copyable step checklist; the reasons behind repo-grounded defect classes
  and held-out PRs; a fix-and-rerun loop on `check` before benching.
- **Added:** step 8, a read-only wrap-up retro via `skill-retro`, grounded in this
  skill's own steps. Applying findings stays a separate PR.
- **Added:** `evals/evals.json` with three prompts: a new expert (crdt), a variant
  (mux), and a casual ask (rusty_tick).

## v1.0.0 — Initial release
**2026-10-30**

- **Added:** the skill with `python3 -m orchestrator new/mine/codex/check`
  ([#3](https://github.com/baileyrd/Expert-Agents/pull/3)); variant comparison on the
  same cases, copying `expert.toml` with the variant.
