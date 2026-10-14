# Research harness improvements: diagnostic measurements

## Scope

These measurements use synthetic developer fixtures. They do not establish real-world research quality, independent relevance truth, statistical superiority, or equivalent tools to normal Codex.

## Bottlenecks

Prior private continuation records included 7 reading calls (~220 s), 5 outline calls (~618 s), and 4 verification calls (~876 s), including inherited attempts, retries and a model switch. These overlapping runs are diagnostics, not independent samples. Acquisition-first scheduling delayed synthesis; lexical hard filtering excluded conceptual matches; title/headings/uncertainties were outside extraction coverage.

## Controlled checkpoint regression

Same four-source offline workload, semantic selection disabled in both arms: no checkpoint required 4 readings before the first draft; checkpoint=2 required 2. Both arms subsequently read all 4 sources and produced a current verification. This is an operation-count result, not a measured model latency improvement. The checkpoint may add synthesis/review cycles and increase total work.

## Frozen candidate selection (k=2)

| Case | Lexical recall | Semantic recall | Semantic counter recall | Model seconds |
|---|---:|---:|---:|---:|
| application-limits | 0.00 | 1.00 | 1.00 | 23.39 |
| checking-burden | 0.00 | 1.00 | 1.00 | 23.96 |
| research-history | 0.00 | 1.00 | 1.00 | 25.19 |

All 12 candidates are fictional metadata. Cases deliberately stress terminology differences and lexical distractors. Developer labels were withheld from the model. One low-effort gpt-6.1-sol call per case; no independent expert labeling. Selection code hashes remained unchanged during the trial. The lexical scorer reused the preexisting harness lexical path with declared terms. Results cannot be generalized to actual literature recall.

Lexical elapsed: 0.514 s. Semantic model elapsed sum: 72.53 s.

CLI-reported input tokens: 36,025; output tokens: 1,590; cached input tokens: 0. This overhead is part of the tradeoff, not evidence of total research-loop acceleration.

## Reproduce

```bash
python -m unittest discover -v
loop-research eval selection --output .research/selection-new --model gpt-6.1-sol --k 2
loop-research eval run --output .research/paired-new --model gpt-6.1-sol --review-model gpt-6-sol --seconds 1800 --methods baseline harness harness-no-checkpoint harness-lexical harness-no-citations
loop-research eval packet --output .research/paired-new --review-model gpt-6-sol
```

The full paired/ablation model study and independent human scoring have not been run for this change. The runner, method counterbalancing, fixed-source resume guard, arbitrary-method blind packet and descriptive metrics were regression tested. All methods retain fact verification, separate reviewer roles, provenance and human finalization.

## Remaining risks

Semantic triage is provisional and bounded: at most 60 metadata candidates, or uniformly sampled 60 pages with beginning/end snippets. It can miss important text in unsampled pages or middle sections. The model can misclassify relevance and support. Exact source spans, context retention, expanded coverage and review catch some errors but do not prove academic support. Independent expert reference sets and repeated paired runs remain necessary.
