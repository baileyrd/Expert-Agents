# Runner

Not built yet. It will check out the target repo at a case's pinned head in a fresh
worktree, run one prompt variant N times per case, and save each reviewer report for
grading.

The plan is to adopt [promptfoo](https://www.promptfoo.dev/) here (its Claude Agent SDK
provider, `--repeat` for N runs, variant comparison and CI integration) and plug
[the scorer](../scorer/) in as custom assertions. That adds a Node-based dependency, so
it waits for the owner's go-ahead. Until then, trials are run by hand or by the
orchestrator and graded into run files as [bench/README.md](../README.md) describes.

Ablations drop one `[[section]]` of an expert's `expert.toml` at a time; sections marked
`required` (authority, pinning, the return contract) are never dropped.
