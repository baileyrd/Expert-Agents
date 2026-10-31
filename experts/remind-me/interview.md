# Interview

Asked in the target project: "If you had to summarize this project in 5 words what would
they be? And if we wanted to recruit an expert for this domain, what would we look for?"

Answer (Nano, 2026-10-31), verbatim:

> 1. In five words: "Shared memory for every agent."
> What to look for in an expert: someone who has built retrieval and memory systems for LLM agents in production. In practice, that means:
>    * Knows retrieval inside out: embeddings, hybrid vector plus keyword search, reranking, and choosing and serving embedding models (the Ollama vs. OpenAI-style `/v1/embeddings` issue we hit is typical).
>    * Knows how memory is modeled: turning raw sessions into small facts, merging duplicates, tracking what each fact is based on, catching contradictions, and letting old facts go stale. Background in knowledge graphs or entity resolution helps a lot.
>    * Has shipped agent tooling: MCP or similar tool protocols, and designing a small tool set that models actually find and use well.
>    * Is solid on systems: Rust and async with tokio, SQLite or Postgres with pgvector, self-hosted deploys, and OAuth for remote connectors.
>    * Cares about privacy: keeping secrets out of stored memory and controlling who can read what across surfaces.
> The rarest skill is the memory-modeling one, so I'd screen hardest for it. Plenty of people can stand up a vector database, but few have dealt with facts drifting and contradicting each other over months. A good interview question is: "How would you keep a memory store accurate as decisions get reversed?"
> 2. Remindme  Rusty-Mill/rusty-remind-me

## Profile

- **Scope**: `crates/` in Rusty-Mill/rusty-remind-me: `remind_me_core` (store, retrieval,
  consolidation, embedder, redaction, sync), `remind_me_mcp` (the tool contract),
  `remind_me_remote` (Streamable HTTP and OAuth), `remind_me_api`, `remind_me_hub` and
  `remind_me_cli`.
- **Rarest trait, first axis**: memory modeling, so the first oracle after this repo is
  its own record of how a memory changes: the ADRs on supersession, evidence, tombstones
  and decision follow-ups (0024, 0027, 0031), and the consolidation and sync code.
- **Traits to axes**: memory modeling is *Memory truth*; retrieval is *Retrieval
  fidelity*; agent tooling is *Tool contract*; systems is *Durable replication*
  (storage seam, outbox, LWW, threads and tokio); privacy is *Containment* (redaction,
  sensitive flags, OAuth and who reads what). Interviewing for "decisions get reversed"
  shapes the Predict step: name how each changed fact is superseded, evidenced and
  synced before reading the PR.
