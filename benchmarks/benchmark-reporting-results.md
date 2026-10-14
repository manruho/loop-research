# Benchmark reporting: one-question real-literature pilot

Date: 2026-10-14. This is a diagnostic pilot, not an independently scored research-quality study.

## Frozen protocol

The question asks whether language-model benchmark reports should disclose tested conditions where use should be avoided, alongside aggregate scores. It explicitly makes a limited normative proposal, without claiming measured user benefit or a novel evaluation method.

The [predeclared protocol and seven references](benchmark-reporting-pilot.json) were committed at `28ff7dd` before the three-arm run. Each arm received the same question, HELM v2 as its only seed, requested writer `gpt-6.1-sol`, and a 600-second limit. Harness review/verification requested `gpt-6-sol`. The seven reference IDs and labels were withheld from research prompts. The baseline uses ordinary Codex tools; the harness uses bounded roles and host discovery. Tools and models are therefore not identical. Requested model names are recorded; backend model resolution is not independently confirmed.

References were curated by the assistant from primary abstracts/metadata. They are a limited diagnostic set, not exhaustive prior art, expert relevance truth, or full-text support. The two counter/caveat references concern transparency and reliance in their tested settings; they do not directly test the proposed reporting obligation.

## Initial frozen comparison (`28ff7dd`)

All three runs completed without changes to program source. Each method was run once, sequentially. Failed and unavailable outputs remain in the evaluation.

| Method | Status | Wall seconds | Draft | Selection calls / seconds | Reference IDs discovered / read / cited |
| --- | --- | ---: | --- | ---: | --- |
| Ordinary Codex | completed | 230.3 | available, unapproved | not instrumented | unknown / unknown / 2 of 7 |
| Harness | budget_exhausted | 600.3 | none | 4 / 508 | 1 / 1 / 0 of 7 |
| Harness without checkpoint | budget_exhausted | 600.5 | none | 3 / 479 | 2 / 1 / 0 of 7 |

The baseline produced the only draft in this comparison. Its identifiable references included five works outside the predeclared set, including [Utility is in the Eye of the User](https://aclanthology.org/2020.emnlp-main.393/) and [AI and the Everything in the Whole Wide World Benchmark](https://arxiv.org/abs/2111.15366). Set membership alone cannot rank manuscript quality or establish citation support. The baseline's discovery/reading activity is not instrumented comparably; unknown is not zero.

The harness spent 85% of its wall time on selection, and the no-checkpoint arm spent 80%. Citation-graph growth changed the candidate pool and repeatedly triggered semantic triage before acquisition. Page selection for the 60-page seed generated long judgments. These observations motivated the repairs below. With both harness arms failing before a draft, this trial cannot establish a checkpoint benefit or compare manuscript quality.

### CLI-reported usage

| Method | Input tokens | Cached input tokens | Output tokens | Reasoning output tokens | Calls with unavailable usage |
| --- | ---: | ---: | ---: | ---: | ---: |
| Ordinary Codex | 405,673 | 343,168 | 4,469 | 436 | 0 reported |
| Harness | 114,945 | 0 | 18,636 | 386 | 1 |
| Harness without checkpoint | 105,526 | 0 | 17,963 | 396 | 1 |

Harness totals include only reported usage; failed-call usage is unknown. These are CLI counters, not inference-compute estimates. Cache behavior, different tools, and incomplete totals prevent a fair cost-efficiency ranking.

## Repairs and regressions (`9e2542b`)

- Fetch/read available material before expanding the citation graph during the first checkpoint cycle. Graph expansion remains available when no material can be acquired/read.
- Default page triage to 16 uniformly sampled pages instead of 60, configurable with `--selection-page-limit`; retain endpoint pages when the limit exceeds one. Require brief selection reasons. Unselected pages are not counted as read, and sampling can miss important text.
- Add conservative `not_equals` ledger checks for explicit negative comparisons of known, same-type scalar records. Missing records, nulls, type mismatches, and fragments lacking negation remain undetermined. This makes an explicit historical/current count distinction expressible; it does not solve arbitrary temporal fact extraction or turn source-span matching into academic support.
- Recognize arXiv HTML and ACL identifiers during measurement. Recompute identifier-only metrics equally for all frozen arms; preserve original reports and do not alter drafts, timings, or statuses.

Four new regressions failed against the frozen implementation before repair. The repaired full suite passed **102 tests**. The regressions also check that page triage creates no reading records, and that a positive number fragment cannot pass a negative comparison.

## Separate post-fix harness run (`9e2542b`)

A fresh harness-only run used the same question, seed, requested models, and 600-second limit. Program hashes matched its frozen manifest at completion. This is a separate implementation trial, not a replacement for an original arm, randomized comparison, or causal estimate.

| Outcome | Observation |
| --- | --- |
| Status / elapsed | budget_exhausted / 600.5 seconds |
| First draft | 597 seconds, 8 role calls |
| Selection | 2 calls / 95 seconds (initial harness: 4 / 508) |
| Exploration / comparison / outline | 102 / 70 / 150 seconds |
| Reading | 1 call, 3,483 characters from HELM page 1 |
| Fact verification | started, timed out after 3 seconds; no accepted verification |
| Independent review / human approval | not reached / absent |
| Reported input / output / reasoning tokens | 142,446 / 19,197 / 618; one call's usage unavailable |

The draft and `paper.tex`, `references.bib`, and `latex-trace.json` were exported. The text identifies its normative premises, limited reading scope, unchecked evidence, and deferred exploration. A draft exists; it is not a verified or reviewed paper. No PDF compilation was performed in this pilot.

The observed selection time fell by 413 seconds, but drafting finished only three seconds before the deadline. Exploration, comparison, and outline now dominate. The next efficiency target is budgeting those roles while reserving verification/review time, without skipping checks or claiming unexamined literature as read. Reduced page sampling may also omit useful evidence. No quality or general speed superiority is established; ordinary Codex remained faster to a draft in this pilot.

## Fixed-pool selection on real publications

The [nine-candidate fixture](benchmark-reporting-selection.json) contains actual publication titles and short assistant paraphrases of primary abstracts, with developer labels withheld from the model. It is not an open-web retrieval test or full-text relevance judgment. The ranking call was run after the initial three-arm study finished.

| Selection, k=5 | Selected | Reference recall | Counter/caveat recall | Observed seconds |
| --- | ---: | ---: | ---: | ---: |
| Lexical | 4 | 4/7 (0.571) | 0/2 | 0.239 |
| Semantic, requested gpt-6.1-sol | 5 | 5/7 (0.714) | 2/2 | 22.361 |

Both selected lists had precision 1.0 against these developer labels. The lexical ranker returned four candidates rather than filling the slot with an unrelated item. Semantic selection used 12,599 reported input tokens and 632 output tokens. This small constructed pool suggests a useful screening difference at additional latency; it establishes neither real search recall nor general superiority.

## Human judgment and reproducibility

The original comparison has a blind scoring packet: two manuscript-unavailable samples and one available manuscript. All human scores remain null/pending. Assistant fact extraction and preliminary inspection are not human scoring. No output has been finalized as an academically approved paper.

Raw runs, databases, drafts, CLI logs, blind mappings, and source hashes are retained locally under ignored `.research/benchmark-reporting-comparison-v1/`, `.research/benchmark-reporting-selection-v1/`, and `.research/benchmark-reporting-after-fix-v1/`. Raw research materials are not committed publicly.

[Aggregate measurements and program hashes](benchmark-reporting-measurements.json) expose the recorded counters without publishing manuscripts, databases, authentication data, or private blind mappings. The case SHA-256 is `d0ccc81d996c7cf5f32b71fa31a61b4e5aa2a976e96227fcd72e6065b0388673`.

Reproduce with fresh output directories at the appropriate commit:

```bash
python3 -m loop_research eval run --output .research/reporting-new \
  --cases benchmarks/benchmark-reporting-pilot.json \
  --model gpt-6.1-sol --review-model gpt-6-sol --seconds 600 \
  --methods baseline harness harness-no-checkpoint
python3 -m loop_research eval selection --output .research/reporting-selection-new \
  --cases benchmarks/benchmark-reporting-selection.json --model gpt-6.1-sol --k 5
python3 -m loop_research eval packet --output .research/reporting-new --review-model gpt-6-sol
python3 -m unittest discover -v
```

A fresh run can differ because model outputs, search services, versions, caching, and network latency vary. Further claims need repeated tasks/runs, independent reference annotation, and human quality scoring. Preserve discovery, acquisition, reading, citation, factual support, and approval as distinct measurements.
