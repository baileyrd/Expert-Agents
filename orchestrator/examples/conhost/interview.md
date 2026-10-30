# Interview

Asked in the target project: "If you had to summarize this project in 5 words what would
they be? And if we wanted to recruit an expert for this domain, what would we look for?"

Answer (Nano, 2026-10-30), verbatim:

> In five words: **a Rust terminal, natively Windows.** The project is Rusty Term, a fast
> terminal with its own console host replacing conhost/ConPTY.
>
> For an expert, I'd look for:
> - **Windows console internals:** ConDrv, the console API (both the old Win32 calls and
>   VT), ConPTY, and how conhost and OpenConsole work. Contributors to microsoft/terminal
>   are the ideal pool.
> - **Terminal emulation:** VT/xterm parsing, grid and scrollback models, Unicode width,
>   and SGR. Alacritty, WezTerm or Ghostty contributors fit well.
> - **Systems Rust on Windows:** unsafe FFI, the windows-rs crate, IOCTLs, handles,
>   process and job objects, and async I/O.
> - **Testing discipline:** fuzzing, conformance tests (vttest, esctest), and performance
>   benchmarking.
>
> The rarest and most valuable trait is the first one. Plenty of people know terminals,
> but very few know what conhost does under the hood.

## Profile

- **Scope**: `crates/libs/ui/rusty_conhost`, `crates/libs/platform/platform_windows`
  (ConDrv), and the rusty_term_core parser where the host reuses it.
- **Rarest trait, first axis**: Windows console internals, so conhost's own source is the
  first oracle after this repo.
- **Traits to axes**: console internals is *Parity*; terminal emulation is *Stream
  fidelity*; systems Rust on Windows is *Hostile client* (containment, caps, custody of
  handles and the child). Testing discipline is not an axis: it is the Tests step and
  the evidence list (conhost's own tests, vttest, esctest).
