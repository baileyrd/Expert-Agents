# Interview

Asked in the target project: "If you had to summarize this project in 5 words what would
they be? And if we wanted to recruit an expert for this domain, what would we look for?"

Answer (Nano, 2026-10-31), verbatim:

> In five words: **replace tokio with our runtime**.
>
> For an expert, I'd look for someone who has built an async runtime or worked deeply inside one, such as tokio, async-std, smol, glommio or monoio. Concretely, that means:
> - **Executor and waker internals.** They should know Future, Waker and Pin by heart, and know how cancellation works. The bugs we hit were dropped wakers in RwLock and cancel bugs in Mutex and Semaphore.
> - **OS I/O layers.** They should know epoll, kqueue, IOCP and io_uring, including Windows quirks. The io_uring hang and the Windows test failures show why.
> - **Interop with tokio-only libraries.** sqlx, tokio-postgres and reqwest only run on tokio. That's the problem `install_fallback()` solves, and it's what remains in rusty_db and rp-router.
> - **Large workspace migrations.** They should be able to plan the order crates move in, decide where tokio stays, and change public APIs carefully.
>
> A good signal is someone who has contributed to the tokio or mio internals, or shipped a production runtime. Experience with loom or other concurrency testing is a strong bonus.

## Profile

- **Scope**: `crates/libs/async/rusty_tokio` (with `rusty_tokio-macros`),
  `crates/libs/async/rusty_stream`, `docs/research/ASYNC-RUNTIME-INVENTORY.md`, and any
  PR that moves a crate from `tokio` to `rusty_tokio` (#686 and its family tickets).
- **Rarest trait, first axis**: the answer names none as rarest, so the first trait,
  executor and waker internals, leads. Its first oracle after the repo is tokio's own
  source and tests, at the version in `Cargo.lock`.
- **Traits to axes**: executor and waker internals is *Wakeups and cancellation*; OS I/O
  layers is *Reactor lifetime*; interop with tokio-only libraries is *tokio parity*
  (a mirrored API keeps tokio's documented contract, and tier C code still reaches a
  tokio reactor); large workspace migrations is *Migration safety* (a move changes the
  runtime and nothing else, in the inventory's order).
  Loom and concurrency testing is method, not an axis: it shapes the Tests step
  (hand-polled futures, controlled interleavings, a test red at base).
