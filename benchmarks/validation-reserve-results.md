# Validation time reservation: bounded real-task replay

## Motivation and policy

The [previous pilot](benchmark-reporting-results.md) produced a draft only at second 597 of a 600-second run, leaving three seconds for verification and no independent review. This change targets that observed bottleneck; it does not change factual acceptance criteria or human finalization.

The initial implementation `426ce764f821e43d062728d9d45367e2e293d8d8` adds the `validation-reserve-v1` policy to time-bounded runs:

- Reserve 30% of the invocation's total time for verification and 15% for review. Each reservation is capped at the configured per-call timeout.
- A writing/planning call may use at most the remaining time minus both reservations; verification preserves the review reservation. All calls retain the configured per-call ceiling and invocation deadline.
- When that envelope is exhausted, stop with `budget_exhausted`. Do not skip an unfinished stage, fabricate an artifact, or borrow validation time to extend writing.
- Prefer verification and independent review of an existing current draft before additional discovery.
- Use concise planning prompts: two competing positions, a three-section argument plan, and a 600–900 word provisional draft retaining all seven required section roles, counterarguments, assumptions, and limitations. These are working targets, not automatic quality judgments. Verification and review prompts and gates are unchanged.
- Persist the policy, invocation total, remaining time, reservation, and per-call envelope in task configuration. Remaining seconds are excluded from semantic cache keys. Reset transient deadlines after an invocation so manual work and fresh resumes are not bound to an expired clock.

A subsequent implementation, `acc2938`, keeps the same allocation but records `validation-reserve-v2`: it uses brief English analytical notes with explicit total word targets, and also preserves reservations before a new cycle after an existing review. Original-language source quotations remain unchanged; verification/review prompts are unchanged.

This is a wall-time envelope, not a guarantee of successful generation. A timeout may leave reserved time unused because downstream work has no valid input. Network latency, CLI startup, and provider variability also consume wall time. Untimed runs and individual manual stages keep their original prompt behavior.

## Regressions

The full local suite passed **115 tests**. Thirteen new tests cover allocation/caps, independent-model routing under the envelope, stable cache keys as the clock advances, stopping without a draft rather than bypassing validation, history-preserving resume, prioritizing an existing draft's audit, deadline cleanup, phase timeout classification, keeping reservations when starting a new research cycle after review, compact selection cache consistency, and bounded contract repair without repeating transport failures or bypassing the review reservation. Synthetic fixtures establish software behavior, not research quality.

## Replay protocol

The fresh runs use the same [question and predeclared seed](benchmark-reporting-pilot.json), requested writer `gpt-6.1-sol`, verifier/reviewer `gpt-6-sol`, and 600-second limit as the prior pilot. The limited seven-reference set is withheld from the research prompts. Program source is frozen during execution. No simultaneous auxiliary model calls are made.

The model names are requested identifiers, not independently confirmed backend identities. The roles use fresh contexts and distinct requested models from the same provider/family; correlated errors remain possible. Human judgment is still required.

The implementation and working-draft length target differ from the prior run. Search results, model sampling and network latency may also differ. These single-run replays are diagnostic, not a randomized causal estimate, superiority study, or human quality score.

## First fresh attempt: protected time but no draft

The initial `v1` run stopped at **330.4 seconds**, with no draft. Selection took 101 seconds, reading 70, exploration 60, comparison 44, and outline timed out after 13 seconds. The 270-second validation reservation remained unused because there was no valid manuscript to audit. Source hashes matched the frozen manifest at completion.

This is a limitation, not a successful research outcome. The former implementation had at least saved a draft at second 597. A rigid protected tail can reduce draft availability when preparation overruns; shorter prompts alone are not a guarantee. The attempt is preserved rather than discarded from the comparison. Its reported input/output/reasoning tokens were 98,059 / 8,139 / 517, with one call's usage unavailable.

## Continuation from the previous draft, on a copy

A separate `v2` continuation started from a copy of the previous pilot's unverified manuscript and its complete research database. It inherited the previous research effort and draft; it is **not** evidence of a fresh draft or end-to-end research completed in this time.

| Stage | Observed seconds | Outcome |
| --- | ---: | --- |
| Fact/ledger verification | 110 | Judgment artifact saved; partial/undetermined/incomplete checks remain |
| Independent review | 33 | `revise`; unsupported compound record claims, missing source qualification, and insufficient prior-work reading flagged |
| Subsequent investigation | remaining active time | New searches and acquisition/page selection; next reading timed out |
| Invocation total | 330.2 | `budget_exhausted`; no automatic approval |

The first review was saved about 143 seconds after the continuation started. Host gates added major findings for unresolved verification checks; a completed verification *call* does not mean that all assertions passed. Review and verification remain in history. Further discovery changed the research basis, so the earlier draft and verification were no longer **current** at the end. Neither historical artifacts nor the original database were overwritten; program hashes and the original database hash remained unchanged.

This demonstrates audit-before-discovery and continuation after criticism. It does not establish manuscript quality or complete the requested substantive repairs. The continuation's reported input/output/reasoning tokens were 127,908 / 12,859 / 469, with one call's usage unavailable.

## Fresh English-profile run and contract failure

At `acc2938`, the fresh `v2` run saved a draft at **268 seconds** (eight role calls), compared with 597 seconds in the previous pilot. Preparation phases were orientation 21, selection 71, reading 30, exploration 24, comparison 23, outline 30, and draft 62 seconds. The draft-length target and analytical language changed, so the observed time reduction is not a quality improvement or a controlled causal effect.

Verification returned a paraphrased assertion span for F006/B003 rather than an exact manuscript substring. The host correctly rejected the entire result. The run stopped at **329.7 seconds**, `needs_human`, with a current draft but **no accepted verification or review**. No approximate matching or gate relaxation was used. Reported input/output/reasoning tokens were 149,544 / 12,187 / 806; all calls reported usage.

## Bounded contract correction

Commit `f6a314a` adds one automatic correction attempt during a bounded loop when a returned verification result is rejected by the host contract. The unchanged input snapshot must match before failure feedback is supplied. The failed result stays in history and is never applied. The corrective prompt requires exact spans, all extraction targets, original uncertainty and unchanged support standards. There is no retry for a valid but unsupported judgment, transport/authentication failure or timeout. A second contract failure stops; the retry cannot consume the review reservation.

A separate continuation copies the failed `v2` database and gives verification plus review **270 seconds and two steps** under the repaired code. Its source study remains unchanged. This is a new invocation after a code repair, with a fresh reservation calculated from 270 seconds; it is not a single frozen 600-second end-to-end success. Time spent diagnosing/fixing code between invocations must not be hidden by adding only their active runtimes.

The correction continuation completed both calls in **126.2 seconds**: verification 89 seconds and review 37. The same draft remains current, as does its accepted verification artifact. Literature judgments were 3 supported and 4 partial; ledger judgments were 17 supported and 3 undetermined. The review decision was **`revise`**. Host gates retained major findings for partial/undetermined judgments. The invocation ended `budget_exhausted` at its explicit two-step limit, with no approval or claim of completion. The original failed result and source database remained unchanged; program hashes matched the frozen repair manifest.

The reviewer still requests substantive prior-work reading and repairs to scope/support. One unresolved host limitation is that `discovery_deferred` is supplied outside the ledger records, while the extracted ledger checks request it inside those records. It remains undetermined rather than being silently accepted. These are follow-up research/verification issues, not reasons to declare this manuscript ready.

Reported input/output/reasoning tokens for this continuation were 49,135 / 8,723 / 112, with no unavailable usage. These counters are not inference-compute estimates. Each continuation's metrics include only new tasks, not inherited calls.

## What the measurements establish

| Observation | Established here | Not established |
| --- | --- | --- |
| Protected validation time | Time envelopes enforced; early stop and unused reservation recorded | A fresh manuscript always finishes before the reservation |
| Concise working profile | Fresh draft at 268 seconds versus prior pilot's 597 | Equivalent manuscript content/quality or causal/general speed advantage |
| Audit before discovery | Previous draft audited in 143 seconds before new investigation | A newly researched paper completed in 143 seconds |
| Contract correction | Exact-span rejection preserved; separate corrected verification/review completed | One frozen unattended 600-second end-to-end success |
| Scientific quality | Partial/undetermined checks and major findings retained; no finalization | Academic validity, human approval, or superiority over ordinary Codex |

[Aggregate counters, statuses, and source hashes](validation-reserve-measurements.json) retain all attempts. Both Python 3.10 and 3.12 CI passed, and the final local suite passed 115 tests. Further time allocation changes should be evaluated across more questions and repeated runs with independent quality scoring, rather than optimizing this one pilot's completion rate.

```bash
python3 -m loop_research eval run \
  --output .research/validation-reserve-new \
  --cases benchmarks/benchmark-reporting-pilot.json \
  --model gpt-6.1-sol --review-model gpt-6-sol \
  --seconds 600 --methods harness
python3 -m unittest discover -v
```

Raw attempts and continuations are retained locally in the ignored `.research/benchmark-reporting-validation-*` directories. Manuscripts, databases, private logs and authentication material are not published. Human scientific quality scoring remains pending; no paper is finalized automatically.
