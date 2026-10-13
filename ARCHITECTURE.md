# CLI research harness

## Responsibilities

The outer harness owns durable research state and accepts or rejects results. Codex performs one bounded role per invocation, using ChatGPT authentication, isolated context and a structured output contract. It does not independently mark evidence as human verified or finalize the study.

The normal path is orientation, search, acquisition, reading, exploration, comparison, outline, draft and review. Review and comparison can request targeted research. Redesign can revise, fork, merge, research, hold or withdraw. There is no requirement to produce a paper.

## Implemented requirements and evidence

| Requirement | Implementation | Verification evidence |
| --- | --- | --- |
| Persistent questions, claims and revisions | `store.py`, `workflow_store.py` | Topic change isolation; fork/merge and revision tests |
| Literature discovery and bounded novelty | `literature.py`, `workflow.py`, `roles.py` | Real arXiv/Crossref searches; unread-overlap rejection test |
| Traceable evidence | `literature_store.py`, `roles.py` | Exact quotation rejection, source-linked draft and export tests |
| Original source and extraction versions | Raw bytes, SHA-256, extractor, physical PDF page | Schema migration preserves old pages and evidence foreign keys |
| Position exploration | Profiles, counterarguments, assumptions, comparison | Two-candidate end-to-end test; real comparison execution |
| English manuscript | Structured blocks, claim/evidence references, host bibliography | Full manuscript and export integration test |
| Critical review and upstream redesign | Separate Codex invocation, issues, prior-issue reassessment | Review/revision/re-review test; hiding prior major issues rejected |
| Human authority | Guide, select, pause, source checks, import, finalize | Human edits retain history; finalization requires fresh review and checked sources |
| Durable execution | Input snapshots, fingerprints, ownership, lock, budgets | Late-result rejection, resume, timeout, pause and parent-death tests |
| Legitimate negative outcomes | Held and withdrawn state; uncertainty export | No-paper outcome tests |
| Reusable outputs | Atomic export, JSON, Markdown, BibTeX, manifest hashes | Export integrity and overwrite refusal tests |

The verification suite uses explicitly synthetic fixtures and an offline CLI shim where deterministic process behavior is required. Real subscription execution and real literature acquisition are additional integration checks. Neither is an evaluation of academic accuracy.

## State and correctness boundaries

An artifact is current only when its input basis still matches the research state. A successful subprocess exit alone is insufficient: schema, references and role-specific semantics are validated before transactional application. Failed results remain inspectable. Human changes during inference make its result stale.

PDFs use Poppler reading order, with physical page boundaries retained and whitespace normalized. Text extraction can still be wrong. A new extractor snapshot does not rewrite old quotations. Reading is limited to selected pages; the exact inspected scope must accompany novelty and factual assertions.

`ready_for_human` is a request for review. `finalized` records a human decision after the host checks current artifacts and cited evidence. Neither is a journal acceptance or proof of correctness. GUI, OCR, figure interpretation and additional discovery providers are later extensions of this CLI state model.
