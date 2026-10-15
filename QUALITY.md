# 研究品質の検証

## 探索

arXiv、Crossref、OpenAlexの検索を使い、問いの同義語・隣接領域・競合と反証を探す。OpenAlexの参考文献と被引用文献は、提供元が報告した引用関係として保存する。引用関係は内容の支持ではない。

各検索には問い、目的、要求件数、問い合わせURL、応答、日時、終了状態を記録する。引用探索には種文献、方向、打ち切り、予算により省いた件数を記録する。種文献は初期検索の発見順から選び、深さ1で探索する。これは被引用数による偏りを避ける単純な方針で、重要性の判定ではない。取得対象は語句との関連性、探索経路、追加調査の目的を考慮して順位づけする。

DOIなどの確実な識別子とOpenAlexの別名を保存し、タイトルが似ているだけでは統合しない。arXivは識別子から対応するDataCite DOIで照会できるが、本文の版は元の資料に保持する。提供元の書誌が異なる版をまとめている場合、その関係を本文同一性の保証と扱わない。

無料キーは任意の`OPENALEX_API_KEY`で設定し、Authorizationヘッダーで送信する。問い合わせURLにキーを載せない。ホストが資料サービスへ送り、Codexへは渡さない。ローカル呼び出しは1秒以上の間隔で直列化し、429と一部の一時障害だけ最大2回再試行する。別ホストへのリダイレクトには認証ヘッダーを渡さない。課金プランへの変更や自動購入は行わない。

## 事実主張と原資料

- 草稿の段落IDは保持し、検証成果物ごとにF IDを発行する。事実主張は草稿の本文または留保欄の正確な連続範囲に対応し、どちらの欄かを明示する。
- 根拠範囲はE ID、D ID、資料ハッシュ、版、ページ、保存本文の文字範囲、引用に対応する。位置はモデルに計算させず、正確な引用と出現番号からホストが確定する。
- 抽出・判定を担う査読モデルは全段落を読み、複合主張、数値、対象集団、条件、否定、因果推論を点検する。主張が規範・仮説の段落に隠れていても抽出対象となる。
- 理論上の定義、論理的議論、明示された価値前提・未検証仮説は経験的事実と区別する。これらの妥当性は査読で検討し、単に新しい実験がないことを理由に規範的提案を排除しない。
- 原資料への対応はホストが検査したE ID・D ID・ページ・ハッシュと引用から確認する。外部URLの存在を支持判定の条件にはしない。
- 判定はsupported、partial、contradicted、undetermined。supportedは「提示範囲が支持するとAIが判断した」であり、人間確認済みではない。
- 草稿に添付されていない根拠を検証器が発見しても、引用不足として残す。モデルの元の判定とホストが加えた変更理由を両方保存する。文献台帳に関連する原文があるだけで草稿の支持を確定しない。
- 全段落の抽出範囲をcomplete、no_facts、incompleteで記録する。completeもAIの自己申告であり、漏れがない保証ではない。査読は草稿自体を再読して抽出漏れを指摘する。

未支持・反証・判断不能な事実と未完了の抽出は重大指摘として保存し、調査・改訂へ戻す。引用の問題だけなら`study revise-draft`で中心主張の版を保持したまま草稿を修正し、再検証・再査読する。論理・射程・貢献の問題は主張の再設計へ戻す。AIが楽観的な査読を返しても、ホストが検証結果の未解決事項を消さない。人間は実際の記述に合わせて事実を限定・仮説化・削除した新しい草稿を取り込み、再検証できる。

登録済み論文の追加読解は `reading_requests` に論文ID・指摘ID・調査目的を保存する。未知のID、重複論文、指摘との対応がない依頼は拒否する。読解・取得の予算を超えず、一波の指定読解後は再設計に戻る。未完了の依頼は消さず、改訂入力に保持する。新資料により旧査読が失効しても、批判の履歴は同じ課題・主張の版の方針として参照する。歴史的査読から最終確定はできない。

探索保留フラグは保存草稿の入力から、論文名は登録メタデータから台帳へ渡す。リンクの台帳は記録範囲だけを示す。`unchecked` と人間の確認行為の不存在を同一視しない。状態値だけで明示的な「not human-verified」等を支持しようとするチェックは判断不能に戻す。この限定的な検査とAI査読は、任意の文章の意味上の支持を証明するものではない。

## モデルと契約

新規の自律研究には異なる`--model`と`--review-model`を明示する。再開は保存値を使う。執筆は既定medium、検証と査読は既定lowだが、利用可能な推論設定はモデルによる。

査読モデルは草稿、出典抜粋、確認範囲、事実検証、過去の指摘を受け取る。執筆側の内部推論を渡さない。同じ提供元であるため共通の訓練・知識による誤りは残り、独立性を保証しない。CLIが実モデル名を報告した場合は記録して指定と照合し、報告がない場合はunconfirmedとする。

入力、実行設定、指示文、出力契約、契約版を保存する。キャッシュはモデルと推論設定、入力、指示文・出力契約・版が一致した成功結果だけを利用する。草稿・根拠・人間方針の変更で旧検証と査読は現行ではなくなる。検証成果物自身を追加しても自分の検証が失効する循環は作らない。

## 移行と最終判断

CLIの版は2.0.0。自律研究のモデル指定を必須にしたため、旧版の起動例に執筆・査読モデルを追加する必要がある。

版7のDBは引用関係、資料識別子の別名、事実主張、根拠範囲を追加する。版1〜6の原資料・引用・草稿・判断履歴は保持する。旧版の確定済み・確認待ち判断は履歴に残し、新しい事実検証と別モデル査読を行うまで研究状態を再確認待ちにする。

最終確定は人間が行う。現行草稿の検証、現行査読、重大指摘への対応、引用する根拠の手動確認を必要とする。内部査読の終了や出力形式の検証は、学術的妥当性や投稿承認を保証しない。

## 未対応・残る限界

OCR、図表・数式の専門的解釈、網羅的な全文読解、異なる出版版の自動同定は未対応。選択したページだけを読むため、出典自体の誤り、別箇所の限定条件、重要研究の未収録、事実抽出と支持判断の誤りは残る。確認範囲と取得不能資料を研究成果の一部として出力する。

## Bounded feasibility runs

The first pilot spent most of its 30-minute allowance before drafting: 19–20
page readings and 44–46 model-based action decisions. Source-only scheduling
now runs deterministically. A small feasibility run should additionally declare
its acquisition and reading limits before execution, leaving time for drafting
and verification. This is a scoped study, not a comprehensive literature review.

For example:

```bash
research --db .research/small.sqlite3 start "Your narrow research question" \
  --model gpt-6.1-sol --review-model gpt-6-astra \
  --paper-limit 3 --pages 1 --queries 1 --search-budget 4 \
  --citation-seeds 1 --citation-limit 2 --citation-budget 2 \
  --effort low --timeout 300 --steps 60 --seconds 1800
```

`--seconds` applies to this invocation, while acquisition/read settings persist.
An exhausted budget preserves partial work and does not mark a manuscript ready.
Resume with an explicit reason and another invocation budget. Human holds,
withdrawals, missing evidence and critical reviews are never bypassed to force
completion. Prompt scope summaries keep query/status and reading boundaries;
full search records remain in the database and exports.

`--queries` limits initial orientation queries. `--search-budget` caps all attempted
keyword searches, including failed searches, fallback providers, and requests
from comparisons/reviews/redesigns. Citation traversal has its separate budget.
Requests beyond the cap stay in their originating artifacts as unresolved work;
budget limits never turn missing evidence into support.

Fact-labeled blocks with no extracted assertions are retained as incomplete
coverage, with the verifier's original status and a host adjustment recorded.
Other validated assertions survive this gate; incomplete coverage still blocks
completion and creates a major review issue. Contradictory coverage and invalid
source/manuscript spans remain rejected. Verification contract version 5 makes
old reports stale for the changed host rule.

Operational resume reasons remain in the audit history but do not change the
scientific input basis. Use `study guide` to give substantive research direction;
explicit guidance, claim changes, source changes and human issue decisions still
invalidate affected artifacts.

Manuscript repair receives the most recent failed repair's validation error only
when the scientific basis and source draft still match. This lets the author
correct contract violations without relaxing source requirements. Reports of
its own workflow use explicitly qualified interpretation blocks, not fabricated
literature citations. Empirical assertions in any block remain subject to fact
extraction and review.

## Execution-record verification (contract 7)

`study verify` now extracts two classes of assertions: literature facts (`F` IDs)
and execution-record assertions (`G` IDs). Both require exact text/qualification
spans and per-block extraction coverage. A host-created snapshot contains search
attempts and provider/status, current-question reading pages, document IDs and
hashes, selected-claim evidence status/relation, and paper inspection scope.
Search counts include failures and citation traversal; keyword and completed
counts are separately named. They are study-wide, not a claim of exhaustive
current-topic coverage. Reading records are filtered to the current question.

For G assertions the model proposes JSON Pointer selectors and typed JSON
expected values, using equality, membership or a nonempty universal comparison.
The host calculates supported/contradicted/undetermined from the captured
records. Unknown paths and inappropriate operations remain undetermined; false
counts cannot become supported because the model says so. Every result retains
observed values, exact manuscript offsets and the snapshot SHA-256. The entire
snapshot remains in the immutable verification artifact and
`ledger-verification.json` export. No database schema migration is needed.

This verifies consistency with stored activity, not independent confirmation
that a tool actually performed research, correctness of external publications,
or completeness of extraction. Mapping natural language to selectors and
extraction coverage are still model judgments, audited by the separate reviewer
and human. Internal records cannot substitute for literature evidence.
Unsupported G assertions and incomplete ledger coverage create blocking major
review issues even when the reviewer proposes acceptance. Literature facts
still require immutable document quotations. Old reports remain historical;
contract 7 verification and contract 4 review must run before finalization.

## Repeated-review handoff

If the same open major/critical issue persists across three distinct reviewed
manuscripts (initial draft plus two unsuccessful repairs) with the same
scientific input basis and review contract, the next automatic action is
`consult`, transitioning to `needs_human`. `study.handoff` records the unresolved
issue IDs and current draft, verification and review IDs. The draft and all
failures/reviews remain available; this is not `ready_for_human` or `finalized`.
Repeated reviews of a single draft do not count as repairs. A new thesis,
substantive `study guide`, source change or contract change breaks this chain.

Use `study status`, `issue list` and `study export` to inspect the handoff.
Respond to issues explicitly, supply evidence, or use `study guide` to redesign
the research before resuming. A bare resume preserves the blocker and does not
silently discard criticism or grant acceptance.

Contract 7 accepts equivalent record-root and `/records/` snapshot-envelope
selectors. Membership checks apply to list values or dictionary keys. Malformed
or nonfinite expected JSON values remain `undetermined` with their original
text and diagnostic retained, without discarding valid assertions. Exact
manuscript/source mismatches and corrupted snapshot hashes still reject the
result. Missing record fields cannot establish a negative assertion.

## Extraction precision (verification 9 / review 5)

Concrete activity claims must be distinguished from epistemic limitations:
"novelty remains unresolved" and "benefits are not established" do not assert
that a recorded action occurred. Claims about what a cited study tested remain
literature assertions. Real claims that the author used no tools or performed
no searches require activity records and remain undetermined if absent.
Verification/review prompts explicitly inspect both manuscript fields, metadata
paper IDs, evidence status/relation and each reading/document-to-paper mapping.
Long compound ledger claims must be split instead of truncating eight checks.

The snapshot can attest whether the stored author input included raw search
responses. New CLI executions save a summary of observable tool-item events
without commands or reasoning text. Unknown event types are recorded, and no
historical zero-tool count is inferred when that telemetry is missing. These
are recorded observations, not independent confirmation of external activity.

A small developer-authored regression set is available through:

```bash
research eval extraction --model gpt-6-astra --output .research/extraction-eval
```

Expected labels are hidden from the model, which sees neutral case IDs. Results
are compared with developer annotations and all attempts are retained. This
measures classification on eleven known regression cases, not full-manuscript
extraction recall, independent human ground truth or superiority over Codex.

Host contract 9 additionally prevents a matching web-search configuration from
establishing tool non-use or exclusive source use. This is a conservative guard
for the observed failure, not a general natural-language entailment prover;
semantic mappings still require review. Ledger-only major findings route to
manuscript repair even when categorized as scope/logic, preserving the thesis.
A stream without item-event inventory does not provide a zero-tool assertion.
