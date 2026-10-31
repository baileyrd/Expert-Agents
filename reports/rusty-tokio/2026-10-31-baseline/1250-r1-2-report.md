<!-- rusty-tokio/1250-r1 run 2, variant baseline, prompt 69ff745b617700af0f2cc0c54b503ddd94cff9c5, model claude-opus-5-5, 2.1.314 (Claude Code); 8 s, 76683 tokens, $1.45 -->

The full `cargo test -p rusty_tokio --all-features --no-fail-fast` run was stopped when it reached its 10-minute background limit, and it never printed a summary. My report already lists it as unfinished.

I didn't restart it, because in this sandbox the all-features runtime can't be built: io_uring setup returns `PermissionDenied`, and every all-features runtime test I did see fail had that cause. A longer run would most likely produce the same sandbox failures, not a result about this PR. The Notify code was still exercised:
- the 4 Notify unit tests pass;
- the sync integration tests pass 125 of 125 with default features, run in a scratch copy of the head;
- the race and tokio-comparison probes ran as reported.

Getting a full all-features result needs a host where io_uring is allowed; that is still a gap in what I could verify. The verdict stays **changes required**, mainly because a `notify_one` can still be lost when another thread drops a `Notified` at the same moment.
