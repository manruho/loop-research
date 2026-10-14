# CLI research harness

## Responsibilities

The outer harness owns durable research state and accepts or rejects results. Codex performs one bounded role per invocation, using ChatGPT authentication, isolated context and a structured output contract. It does not independently mark evidence as human verified or finalize the study.

The normal path is orientation, search, acquisition, reading, exploration, comparison, outline, draft, fact verification and review. Review and comparison can request targeted research. Redesign can revise, fork, merge, research, hold or withdraw. There is no requirement to produce a paper.

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

## Quality upgrade (schema 7)

OpenAlex provides keyword discovery and bounded incoming/outgoing citation observations. `discovery.py` records the provider response and graph edges; it does not equate citations with substantive support. DOI/OpenAlex aliases do not rewrite immutable document versions. The acquisition limit applies to cumulative attempted papers, not a changing top-N list. Deterministic execution follows the already-generated search plan and host ranking for source operations; model decisions remain for other competing research actions.

`verification.py` extracts fact-level spans from every block, including mixed and normative blocks, and assesses sources using a separately selected Codex model. Exact manuscript/source offsets are resolved by the host. A verification artifact (U ID) and its fact/source rows are stored atomically. Unsupported facts and incomplete extraction create blocking issues even if a reviewer overlooks them. Review receives the manuscript and evidence, not the writer's private reasoning.

Prompt contracts, their versions, model configuration and result provenance are saved. CLI-confirmed models are checked against requested models; absent confirmation remains unconfirmed. Different requested models at the same provider do not guarantee independent errors. Legacy completion judgments are retained in the audit trail but require the new verification/review process before reconfirmation.

`evaluation.py` provides a paired three-case pilot, retained failures, bounded runtime, anonymized human score sheets, validated score imports and reports. Automatic identifier matches and native support judgments are not human quality scores. See QUALITY.md and EVALUATION.md for operation and remaining limits.

### Execution records and repeated-review handoff

`ledger.py` constructs a host-owned, checksummed record snapshot for every new
verification input. `verification.py` keeps literature facts and ledger claims
separate: the model extracts exact manuscript spans and record-check expressions,
while the host compares typed expected values to stored observations. Ledger
claims and coverage are saved in the immutable U artifact, avoiding a schema
migration and keeping old artifacts readable. The export includes the snapshot,
checks, observed values and source-span locations in `ledger-verification.json`.
Reviewer contract 4 audits extraction and semantic mapping; unsupported record
claims remain blocking even if model review accepts them.

`ResearchWorkflow.repeated_review_blockers()` identifies the same unresolved
major/critical issue in consecutive reviews of distinct drafts, under the same
basis and contract. After two unsuccessful revisions it chooses `consult` and
records a `study.handoff` event. The status remains `needs_human`; human issue
responses or substantive research changes are required to make progress.

### LaTeX export

`latex.py` renders the immutable structured manuscript into an escaped article
with BibTeX citations, qualifications and uncertainty disclosures. No model
rewriting occurs during rendering. `latex-trace.json` associates TeX line spans
with source blocks, claims, evidence and bibliography keys. Manual-attribution
citations receive E-key BibTeX entries without fabricated authors/dates.

Every export includes TeX; optional `--pdf` invokes LuaLaTeX/BibTeX inside a
separate temporary build directory with shell escape disabled. The resulting
PDF and build log join the same atomically published export and manifest hash
inventory. Export format 2 adds PDF build metadata; existing JSON evidence and
research-state semantics are preserved. Editing rendered TeX does not update
or validate the authoritative research ledger.


## 草稿の台帳事実と探索候補

`ledger_fact`は内部活動の事実を表す段落種別であり、科学的支持を与えない。草稿入力にホストが生成した台帳スナップショットを保存し、検証で原稿の部分文字列からG主張、record path、比較値、SHAへの対応を作る。ledger_factにG抽出がなければホストが抽出範囲をincompleteへ変更する。従来のfactは文献引用必須を維持する。手動草稿インポートも同じ台帳入力と検証条件を使う。draft/revise_draft/verify契約版を更新し、旧タスクのキャッシュ・旧検証判断を再利用しない。

語彙選別方式の文献ランキングは取得上限の配分にだけ使う。DBの候補・資料を削除せず、科学的根拠の採否や新規性を決めるものではない。手動登録はこの選別を通さず、人間の探索方針を維持する。引用探索も同じ候補集合からseedを選び、手動seedを優先する。OpenAlexへの対応付けはDOIまたは正確なarXiv所在識別子に基づき、タイトルだけの自動統合を禁止する。


## 暫定草稿の区切りと選別記録

追加の永続化基盤は導入せず、selectionを既存のtasks/artifactsに保存する（Y prefix）。入力の一致と実行モデル設定でキャッシュし、資料SHA・人間の指示・書誌情報が変われば再選別する。選別は書誌/抜粋段階であり、読解Lや根拠Eを作らない。

研究ループの既定readings checkpointは、初回査読までは追加取得を保留して現時点の根拠から暫定稿を作る。草稿入力のdiscovery_deferredを査読へ引き継ぎ、ready判断でも探索保留中ならactiveへ戻して残りの探索を続ける。主張・草稿・検証のcurrent/basis判定、失敗/予算到達/人間引き継ぎの判定は維持する。手動確定条件を緩めない。

verification_targetsは原稿内のJSONパスと安定IDの対応だけを渡し、本文を入力で重複させない。ホストはverification_blocksでタイトル・見出し・不確実性を含む原文を再構成し、抽出範囲と原文位置を検査する。F/Gのcontext_idから共有assertion_contextsに対応付け、元のフィールド全体を重複させずに残して意味上の査読に渡す。既存の検証契約版を更新し、旧版の完了判断を再利用しない。

評価の除去方式は研究工程の配分・探索だけを対象とし、検証ゲートの緩和を比較方式に含めない。品質評価は既存の人間評価票を全宣言済み方式へ拡張する。CLIの未報告使用量を推測・ゼロ埋めせず、失敗した比較を捨てない。
