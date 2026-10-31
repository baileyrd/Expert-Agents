# Interview

Asked in the target project: "If you had to summarize this project in 5 words what would
they be? And if we wanted to recruit an expert for this domain, what would we look for?"

Answer (Nano, 2026-10-31), verbatim:

> In five words: **personal medical records, organized locally**. MediBoard pulls your health records and Apple Health data out of PDFs and exports, has you review them, and turns them into charts, timelines, and claim-ready exports such as VA claims.
>
> For an expert, I'd look for these, roughly in priority order:
> - **Clinical informatics.** Someone who knows how medical records are structured (HL7/FHIR, LOINC, SNOMED, ICD-10, RxNorm) and can make sure what we extract and normalize is clinically correct.
> - **VA disability claims.** An accredited VSO or claims agent who knows the rating schedule (38 CFR Part 4) and what evidence actually moves a claim. They'd shape the claim view and exports.
> - **Health data privacy and security.** Someone familiar with HIPAA-style handling, local-first storage, and hostile inputs like oversized zip files.
> - **Document extraction.** Experience with OCR and messy, scanned clinical PDFs, which is the hardest technical part of the pipeline.
>
> If you can only bring on one person, pick a clinical informaticist who has also worked on veteran claims. They cover the most ground.
>
> baileyrd/MediBoard
>
> MediBoard Expert

## Profile

- **Scope**: `src/paper_work/` in baileyrd/MediBoard (`extract`, `transcribe`, `ingest`,
  `health`, `domain`, `store`, `api`, `admin`, `web`), `contract/` (schema, migrations,
  API, extraction schema) and `scripts/phi_guard.py`.
- **Rarest trait, first axis**: clinical informatics, so the first oracle after this
  repo is the vocabulary the data claims to follow: Apple HealthKit's type definitions
  for the Health import, UCUM for units, and LOINC, RxNorm and ICD-10-CM where a value is
  coded. The answer's "one person" combines it with veteran claims, so the claim axis
  comes second.
- **Traits to axes**: clinical informatics is *Clinical fidelity*; VA claims is *Claim
  evidence*; privacy and security is *PHI containment*; document extraction is *Source
  and review provenance* (every fact stays paired with the page it came from and the
  human review made of it). Hostile inputs (oversized zips, symlinks, model-written
  keys) shape the Break it step under containment.
