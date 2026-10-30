# Mill Board claim verification

Probe worktrees (mine, not the trial ones) are in
/tmp/claude-0/-home-claude/c6e91f6f-c04b-5b25-b0a9-c1761f25074d/scratchpad/verify-mb/ (wt-1187, wt-1165, wt-1238, wt-main, wt-pre).
I ran `git fetch` in /home/claude/rusty_mill, which moved origin/main from 5991f222 to 65c04a00b. Nothing under
crates/apps/rusty_mill_board changed between the two, so every "on main" statement below holds for both.

## #1187-F2 (P0): grant subjects not namespaced by auth mode

**Class: real. Severity overstated: P2, not P0.** Still present on origin/main.

**What I ran:** I appended `probe_f2_cross_mode_grant` to wt-1187/crates/apps/rusty_mill_board/tests/web.rs. It starts
three `Web`s with `with_oauth` that share one state file, and it passed:
- Identities mode with an agent named `shared` (role qa): an OAuth sign-in gives an access token whose `board_whoami`
  is `{"actor":"shared","role":"qa","authenticated":true}`.
- The same access token on a `Web` running `Auth::Shared` (the `--auth-token` mode) gives 200 and
  `{"authenticated":false, ...}`. That is the unrestricted shared caller, who names their own actor and role, human
  included. So a qa-only agent gains every role.
- The reverse also holds. A grant approved with the shared token, presented to an identities-mode `Web` that has an
  agent named `shared`, acts as that agent.

**Why it happens:** web.rs:136 issues `Subject::new("shared")` for a shared-token grant. web.rs:144 issues the actor
name for an identities grant. The lookup at web.rs:199-205 compares only the subject string. `valid_actor`
(identities.rs:71-76) accepts the name `shared`.

**What the claim gets wrong:** "switches the same settings dir" cannot happen. main.rs:93 and :98 set
`--settings-dir` and `--identities` to `conflicts_with = "auth_token"`. In `--auth-token` mode, `oauth()`
(main.rs:163-180) therefore finds no default path. The operator must pass `--oauth-state` explicitly and point it at
the identities-mode grant file, and must also have an agent literally named `shared`.

**Why P2:** the authority gain is real, but it needs a deliberate, unusual operator setup. deploy/README.md:43-60
documents neither mode switching nor namespacing, so this is not a design question.

**On main:** web.rs:157, :166, :229 and :233 are unchanged. Main binds the state file to the issuer (main.rs:140), but a
mode switch with the same issuer still shares grants.

## #1187-F4 (P2): misplaced constant-time doc comment

**Class: real, P2 is right.** Still present on origin/main.

**Source trace:** at identities.rs:272-280 at the head, the lines "Whose token this is. Compares against every entry,
so timing does not reveal..." now sit directly above the new doc for `by_actor`. They merge into its rustdoc. `by_actor`
takes an actor name, not a token, and uses `find`, which is not constant-time. `identify` (:281) now carries only
"The identity a token belongs to.".

**On main:** origin/main identities.rs:272-279 still has the orphaned two lines heading `by_actor`.

## #1187-F5 (P2): CLI OAuth wiring untested

**Class: real at the head, P2 is right.** Partly fixed on main.

**Source trace, at the head:**
- main.rs has no `mod tests`.
- tests/process.rs and tests/project_export.rs run the binary, but never with `--issuer`.
- tests/web.rs `start_oauth` builds `Web` by hand with its own `allowed_hosts` and state path.

So removing any of these fails no test: the `oauth()` default-path logic (main.rs:162-180), the issuer-host push
(main.rs:322-324), or the `with_oauth` call (main.rs:356-359).

**On main:**
- 1ebd8a897 ("keep each board's OAuth grants to itself") added `default_oauth_state` and the unit test
  `each_board_gets_its_own_default_oauth_state`, so the default path is covered.
- 10927f9d5 moved the host list into `web::issuer_hosts`, which tests/web.rs:345 uses.
- Still uncovered: main.rs:349 (`allowed_hosts.extend(issuer_hosts(issuer))`) and the `with_oauth` call in `main`
  could each be deleted with all tests green.

## #1165-F2 (P2): `tick_store_never_skips_an_event` is flaky

**Class: fixed since, by 61bdc270d.** The defect is real at the head and P2 is right.

**What I ran:** the test binary at the head in wt-1165, 60 times: 54 passed, 6 failed. Every failure was
commit_order.rs:82 `every add committed one event`, with left 321 or 322 against right 320: extra events, as claimed.

**Mechanism:** confirmed by source trace. `insert` POSTs the task (tick.rs:646-660) before `record` takes the events
lock (tick.rs:571-578). A concurrent `observe` (tick.rs:581-605), primed by then, lists the new task with
`seen == None` and pushes a synthesized `Created` by the tick actor. Then `record` pushes the real one.

**Severity:** P2 for a flaky test is fair. The same race also gives real `board_events` pollers a phantom `Created`
event attributed to rusty_tick, which is arguably P1-adjacent, but it is narrow.

**Pass/fail counts on main:**

| Commit | Failures |
|---|---|
| e0a614d21 (the PR's merge) | 2/50 |
| 22a35d8f0 | 4/80 |
| b5b1dda46 (just before 61bdc270d) | 2/200 |

The flake survived on main up to 61bdc270d ("rusty_mill_board: serialize writes with observation"), which makes
observation share the writers' lock so it cannot see a POSTed but unrecorded task. Results after the fix:

| Commit | Failures |
|---|---|
| 61bdc270d | 0/200 |
| origin/main | 0/300 |

**Verdict: fixed since 61bdc270d.** Before the fix the failure rate was about 1%, so 0 in 500 runs (about a 0.7%
chance if it were not fixed), together with the lock change, supports the fix. The PR head's own rate was about
10% (6/60), near the claimed "1 in 6".

## #1165-F4 (P2): /health module doc describes the removed store

**Class: real, P2 is right.** Still present on origin/main.

**Source trace:** main.rs:12-13 says `/health` answers "503 once it needs a restart". That wording came from the
removed mmdb store, whose `health()` was `settled()`. At the head, the only adapters are:
- `MemStore`: the default `Ok(())`, at store.rs:41.
- `TickStore::health` (tick.rs:739-747): 503 whenever rusty_tick's own `/health` is not 200 or cannot be reached.

So /health does still return 503, but only for "rusty_tick down", never for "needs a restart". The claim's "it is never
that" is correct in that sense. ADR-0012 says "If it is down, the board's /health reports 503". The trait doc
(store.rs:37) and board.rs:205 ("must be reopened") are stale the same way.

**On main:** main.rs:13 is unchanged.

## #1238-F4 (P1): `card_add` fails when another writer numbers a stray

**Class: fixed since.** The defect is real at the head and P1 is right. Fixed by 22a35d8f0 ("fix(mill-board): reserve
card numbers durably in rusty_tick"), a later commit on the same PR branch, merged as 912e3c757.

**What I ran:** wt-1238/crates/apps/rusty_mill_board/tests/probe_f4.rs wraps `MemStore` so that the first `list()`
after arming numbers the stray, as a second board would. `board.add(...)` then returned
`Err(Invalid("card is already numbered"))`. Path:
- `insert_numbered` (board.rs:345-355) calls `number_strays`.
- `number_strays` calls `apply` at :338.
- `apply` re-reads the card and calls `Card::numbered` (domain.rs:522-525), which refuses.
- `?` propagates the refusal to the caller's `card_add`.

The `numbering` mutex is per process, so it does not help across boards.

**On main:** `Card::numbered` returns the card unchanged if it is already numbered (domain.rs:518-530), and `apply`
treats an unchanged card as a no-op (board.rs comment "Another board may number the card first").

## #1238-F5 (P2): reserved-tag docs omit `number:`

**Class: real, P2 is right.** Still present on origin/main.

**Source trace:**
- domain.rs:203-209 `reserved_tag` includes `number:`.
- README.md:30-32 lists only `agent`, `board:…` and `claim:…` as "the board's own".
- README.md:73 describes only the `board:`/`claim:` tags in the storage mapping.
- The PR updated the ADR-0012 mapping table (lines 58-59) but not the tag-budget line at
  docs/adr/0012-one-task-store.md:245-246, which still counts `agent`, `board:`, `claim:`, `kind:`, `track:` and
  `migrated:` as leaving 14. With `number:` it is 13.

**On main:** neither file has been updated.
