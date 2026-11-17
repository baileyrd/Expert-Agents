# Open questions — expert-agents
Claude Code: append here instead of stopping when a question isn't blocking.
Format: `- [ ] Q<n> (<date>, blocking? y/n): <question> — interim choice: <what you did>`
Resolved items are checked and point to the D#/QA entry that answers them.

- [ ] Q1 (2026-11-03, n): Conflict. sync/README.md defers a sync script "until there is more than one target repo" (D30), but the `expert.toml`/`developer.toml` files now name five targets (Rusty-Mill/rusty_mill, Rusty-Mill/rusty-remind-me, baileyrd/PythRust, baileyrd/agent_cloud, baileyrd/MediBoard). README.md's expert table also omits bbp-swf and mediboard. Also mem_c7d5ca62ef0e40218e2578d7a43b43d5 (2026-10-31) says prompts and the guard are not yet synced to rusty_mill. Build the script now, and what is synced today? — interim choice: none; sync stays manual per sync/README.md.
- [ ] Q2 (2026-11-03, n): The generic catalog (D32, 58 prompts, delivered 2026-11-01 as expert-catalog.zip) is not in this repo, and the domain-layer format is undesigned. Does it land here, and where? — interim choice: none.
- [ ] Q3 (2026-11-03, n): Tester is the next role (D17), with no tester expert yet. Which domain goes first? — interim choice: none.
- [ ] Q4 (2026-11-03, y for that branch only): Branch `claude/project-thread-6wc511` leaks held-out bench keys and should be dropped (mem_da979abcda1d40ce97d974dfaf134513); it still exists at harvest. Deleting a branch needs Nano. — interim choice: never base work on it.
- [ ] Q5 (2026-11-03, n): A rusty_mux developer trial on a fresh issue was waiting on Nano's go (mem_d4fc39aeb7d246c880092b40a13abafb). Does the bench standing go (D24) cover developer trials that write code? — interim choice: treat it as not covered.
