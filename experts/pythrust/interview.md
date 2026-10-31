# Interview

Asked in the target project: "If you had to summarize this project in 5 words what would
they be? And if we wanted to recruit an expert for this domain, what would we look for?"

Answer (Nano, 2026-10-31), verbatim:

> **Five words:** Python syntax, Rust-grade native performance.
>
> **Expert profile.** Look for a compiler engineer whose experience spans these areas,
> roughly in priority order:
> - **Ownership and memory analysis:** experience inferring ownership or borrows (Rust's
>   borrow checker, Swift ARC optimization, Lobster, Koka/Perceus). It matters most
>   because hiding that work from users is the core bet of the project.
> - **Type systems for dynamic languages:** inference and narrowing for Python-like code
>   (mypy, Pyright, Mojo, Codon), including how optionals and unions get narrowed.
> - **Native backends:** Cranelift or LLVM codegen, lowering exceptions to `Result`,
>   refcount elision, and how runtimes behave across platforms (Windows, other targets).
> - **Language design judgment:** someone who can weigh fidelity to Python against keeping
>   the implementation sound, which is the kind of call the D7 fault question needs.
> - **Rust fluency,** since the compiler and runtime are written in it.
>
> The rare part is ownership inference combined with Python semantics. People with a
> background on the Swift, Mojo or Codon teams, or academics working on Perceus-style
> reference counting, would be the strongest match.

## Profile

- **Scope**: `crates/pythrust-compiler` (every stage: `infer`, `own`, `interp`, `lower`,
  `rc`, `codegen`), `crates/pythrust-rt`, the conformance corpus under `tests/`, and the
  decision docs under `docs/`.
- **Rarest trait, first axis**: ownership inference over Python semantics, so the `own`
  stage's own soundness rules (`docs/research/ownership.md`, D1 to D8) are the first
  oracle after the repo, then Perceus and Swift ARC.
- **Traits to axes**: ownership and memory analysis is *Ownership soundness*; type
  systems is *Type soundness*; native backends is *Backend parity* (native output equals
  `run` and CPython, refcounts balance, every CI target); language design judgment is
  *Python fidelity* (a deviation from CPython is decided and recorded, never accidental).
  Rust fluency is not an axis: it shapes the Checks (`fmt`, `clippy -D warnings`, no
  `unwrap`, `forbid(unsafe_code)` outside the runtime's C ABI).
