# Sync

Prompts are versioned here; target repos only receive copies. Each `[[prompt]]` in an
expert's `expert.toml` names its `target_repo` and `target_path`.

To publish a promoted prompt, copy the file to its target path in a PR on the target
repo, under that repo's contribution rules, citing this repo's commit and the bench
report that justified the promotion. A copy is never edited in the target repo; a
change there comes back here as a variant and goes through the bench.

A sync script waits until there is more than one target repo.
