# Review fixes under crates/libs/ui/rusty_conhost, crates/libs/platform/platform_windows

13 commits answer a review; 8 held-out commits removed.
Read each one's diff (`git show <sha>`) for the defect class it fixed.

| Commit | Subject | Files |
|---|---|---|
| 033b18eb2f | rusty_conhost: read CSI bytes as rusty_term_core does | `crates/libs/ui/rusty_conhost/src/console/screen.rs` |
| 885913461c | rusty_conhost: watch DEC private CSI past the parser's cap | `crates/libs/ui/rusty_conhost/src/console/screen.rs` |
| a4d70b1462 | rusty_conhost: answer Dottius's re-review of 20375966 | `crates/libs/ui/rusty_conhost/src/console.rs`, `crates/libs/ui/rusty_conhost/src/console/screen.rs` |
| 203759668c | rusty_conhost: resolve a parked cursor move when it is emitted | `crates/libs/ui/rusty_conhost/src/console.rs`, `crates/libs/ui/rusty_conhost/src/console/output.rs`, `crates/libs/ui/rusty_conhost/src/console/screen.rs` |
| aadb17b934 | rusty_conhost: answer Dottius's review of 1d2ee3b6 | `crates/libs/ui/rusty_conhost/src/console.rs`, `crates/libs/ui/rusty_conhost/src/console/output.rs`, `crates/libs/ui/rusty_conhost/src/console/screen.rs` … |
| ba79d1b1d7 | rusty_conhost: answer the Codex review of aefc9db1 | `crates/libs/ui/rusty_conhost/src/console/output.rs`, `crates/libs/ui/rusty_conhost/src/console/screen.rs` |
| ca11933902 | rusty_conhost: drain a detached console only for calls blocked on output | `crates/libs/ui/rusty_conhost/src/console.rs`, `crates/libs/ui/rusty_conhost/src/console/read.rs`, `crates/libs/ui/rusty_conhost/src/runtime.rs` |
| 3bfe0b5e2b | rusty_conhost: keep draining parked writes once the master is detached | `crates/libs/ui/rusty_conhost/src/runtime.rs` |
| 1d2ee3b6cd | rusty_conhost: answer the conhost review of aefc9db1 | `crates/libs/ui/rusty_conhost/THIRD_PARTY_NOTICES.md`, `crates/libs/ui/rusty_conhost/src/console.rs`, `crates/libs/ui/rusty_conhost/src/console/output.rs` … |
| 1eb6ec6808 | rusty_conhost: answer the conhost-expert review of host PR 8 | `crates/libs/ui/rusty_conhost/src/console.rs`, `crates/libs/ui/rusty_conhost/src/console/read.rs`, `crates/libs/ui/rusty_conhost/src/ctrl.rs` … |
| 16cd563bda | rusty_conhost: contain short write-back room; keep the child on a lost engine | `crates/libs/ui/rusty_conhost/src/console.rs`, `crates/libs/ui/rusty_conhost/src/msg.rs`, `crates/libs/ui/rusty_conhost/src/pty.rs` … |
| c943f81362 | rusty_conhost: cap the diagnostic dedup set | `crates/libs/ui/rusty_conhost/src/console.rs` |
| b7857aa878 | rusty_conhost: fix the review findings on 961858f6 | `crates/libs/ui/rusty_conhost/src/console.rs`, `crates/libs/ui/rusty_conhost/src/pty.rs`, `crates/libs/ui/rusty_conhost/src/runtime.rs` … |
