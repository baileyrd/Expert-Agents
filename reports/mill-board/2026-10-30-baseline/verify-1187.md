# Verify notes: mill-board/1187-r1, baseline runs 1-5

The head is `2017c08f1f83ab7a181b69574b354376f9ad5e2a`. origin/main is `ebb92e2819b0bc445aaf6f5baeca8fb1343a5332`, fetched 2026-10-30. The K3 fix is `1ebd8a8977b87d5bee41127c5c9d518a7b2f109b`.

I read the code in detached worktrees of all three, which are now removed. Each claim is checked once, against the code and the project docs. I did not use the reviewer's argument. I ran no new probe: each trace below is conclusive from source, and the one claim that needed execution (shared subject) was already probe-tested in `../2026-10-30-frozen1/verify.md`.

## Key matches (Blocking findings only)

| Key | Runs that caught it | Notes |
|---|---|---|
| K1 | 1, 2, 3, 4, 5 (all F1, all P0) | `by_actor` is first-match (identities.rs:276-277); `parse` dedups tokens only (identities.rs:112). |
| K2 | 2 F2, 3 F3, 5 F2 | `host_ok` keeps brackets (web.rs:172-181) while `Issuer::host` strips them. Run 4 mentions it only under Optional and calls it harmless, so it is not scored. |
| K3 | none | See "Key K3" below. |

## Non-key claims

- **Misplaced constant-time doc comment** (run 1 F3, run 2 F3, run 3 F5, run 4 F2, run 5 F3): **real**. At identities.rs:272-273 on the head, `identify`'s "Compares against every entry..." lines sit above `by_actor`, which is a short-circuiting `find`. The lines are still there on main at identities.rs:272-273.
- **CLI OAuth wiring untested** (run 1 F4, run 2 F4, run 3 F4, run 4 F3, run 5 F4): **real**.
  - At the head, main.rs has no tests, and no process test passes `--issuer`.
  - tests/web.rs `start_oauth` builds the host list by hand and sends `Host: 127.0.0.1`.
  - On main, 1ebd8a89 added `each_board_gets_its_own_default_oauth_state`, so the default-path half is covered. Nothing exercises main.rs:349 `allowed_hosts.extend(issuer_hosts(issuer))`; tests/web.rs:345 repeats that line instead of calling it. The claim is therefore not fully fixed on main, and the class stays real.
- **Shared-token grant becomes the agent named `shared`** (run 3 F2, graded P1): **real, but P2**.
  - web.rs:136 issues `Subject("shared")` and web.rs:144 issues the actor name. web.rs:199-205 compares the bare string. `valid_actor` (identities.rs:71-76) accepts `shared`.
  - The frozen1 grader ran this as a probe test and it passed. The code is unchanged at the head, and it is still present on main (web.rs:157, :229). Main's issuer binding does not separate two modes that use the same issuer.
  - The run's trigger, "keeps the default oauth.json in --settings-dir", cannot happen. main.rs:93 and :98 make `--settings-dir`/`--identities` conflict with `--auth-token`, so the operator must pass `--oauth-state` explicitly. Hence P2.
  - Run 1 and run 5 raise the same point under Optional only.
- **OAuth access token accepted on `/api/settings_*`** (run 1 F2, graded P0): **design_question**.
  - web.rs:118-119 documents `with_oauth` as accepting "the access tokens it issues on `/api` and `/mcp`", and `identify` (web.rs:184-189) serves both routes on purpose. The module doc (web.rs:5-8) and deploy/README.md:50-51 ("acts as that token's agent") agree.
  - It is unchanged on main (web.rs:136).
  - The ADR-0004 and RFC 8707 audience argument is a reasonable objection to the design, not a defect against the documented intent. The human reviewer did not raise it. Runs 4 and 5 themselves list it as a design question.

## Key K3

**Conclusion: the key is accurate, and I left it unchanged.**

**The human review.** `GET /repos/Rusty-Mill/rusty_mill/pulls/1187/reviews` returns review 5673834930 on commit `2017c08f`, a COMMENTED review titled "Additional security isolation blocker". It says:

- main.rs:168-178 picks `--oauth-state`, else `--settings-dir/oauth.json`, else `parent(--identities)/oauth.json`.
- So `/cfg/a.json` with issuer A and `/cfg/b.json` with issuer B both use `/cfg/oauth.json`.
- `Provider::subject_of` checks no issuer binding, so A's grant resolves against B's credentials when actor names match.

The review gives no P-level, only "blocker". The key's P1 is a fair reading of that word. `/pulls/1187/comments` adds nothing on this point.

**The cited lines.** At the head, main.rs:168 is `let state = cli` and main.rs:178 is the `.ok_or(...)` line. The beside-identities fallback, `i.with_file_name(OAUTH_STATE)`, is at main.rs:172-177. The key's "main.rs:168-178" is exact.

**The fix commit.** 1ebd8a89, "keep each board's OAuth grants to itself", does what the key says:

- It adds `default_oauth_state`, which picks `<identities stem>.oauth.json` beside `--identities`.
- It adds `StateStore::bind_issuer`, so a board with a different issuer on the same file refuses to start.
- It adds the tests `each_board_gets_its_own_default_oauth_state` and `a_grant_file_belongs_to_one_board`.
- Its message describes the K3 scenario word for word.

Two later CHANGES_REQUESTED reviews (5674011534, 5674028622) tightened the binding further, on commits after this head. That does not change K3 at this head.

**Is the layout supported?**

- `--identities FILE` without `--settings-dir` is the documented setup. docs/connecting-models.md:13-20 puts the file at `~/mill-identities.json`.
- A second board on the same host is documented too: connecting-models.md:86-87 says `mill-runner` needs "a board of its own".
- Two boards whose identities files share a directory (for example `$HOME`) and have no `--settings-dir` is therefore an ordinary setup. Both default to the same `oauth.json`.
- `--issuer` and `--oauth-state` are new in this PR, so the collision is introduced by it.

**What the runs said, by section.** No run puts K3 in Blocking findings, so the hit rate is 0/5.

| Run | Where it appears | Matches K3? |
|---|---|---|
| 1 | "Checks and validation gaps" | No. It mentions two boards sharing one `oauth.json`, but through the *same* `--identities` file, framed as missing cross-process locking and "out of this PR's scope". |
| 2 | Optional improvements | Yes in substance: the default "beside `--identities`" lets two boards share one `oauth.json`, and "a token from one board's issuer is accepted by the other". It was graded non-blocking, so per the rubric it is not scored (a comment in 1187-r1-2.toml says so). |
| 3 | Scope note | No, it rules it out: `oauth.json` "is per board". |
| 4 | Failure classes | No, it rules it out: `oauth.json` "defaults to that board's settings dir", missing the identities fallback at main.rs:172-177. That is a wrong trace. |
| 5 | none | Not raised. |

The brief said K3 was "caught 2/5 by the rubric". That count includes runs 1 and 2's non-blocking mentions, and run 1's mention describes a different mechanism. Scored on Blocking findings alone, K3 is caught 0/5. Even a lenient reading of non-blocking sections gives only run 2. I did not relax the key.

## Uncertain

- Run 3 F2 has severity P1 against my P2. Severity is not scored for non-key findings, so this does not change the score.
- Run 1 F2 being a design question is a judgment call. The code documents `/api` acceptance as intended, so I did not count it as a false positive. The settings-minting consequence is not explicitly documented.
