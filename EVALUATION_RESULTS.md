# 開発中の予備評価（2026-10-13）

## 条件

公開課題3件、各方式1回、計6実行。執筆モデルはgpt-6.1-sol・medium、ハーネスの査読モデル指定はgpt-6-astra・low。各実行の時間上限は1800秒。開始文献と英語草稿の目標は共通。

**開発中のコード変更と1件の中断を含むため、固定版の厳密な比較ではない。** 初期実行にはソースハッシュが欠ける。生のDB・実行記録はローカルに保持した。

## 結果

| 課題 | 方式 | 終了状態 | 経過秒 |
| --- | --- | --- | --- |
| research-history | baseline | completed | 239.5 |
| research-history | harness | needs_human | 1801.0 |
| checking-workload | harness | interrupted | 未記録（中断） |
| checking-workload | baseline | completed | 168.6 |
| contextual-evaluation | baseline | completed | 136.6 |
| contextual-evaluation | harness | needs_human | 1800.9 |

通常のCodexは3件とも最終原稿を返した。ハーネスでは2件が30分の上限で草稿に到達せず、1件が制御プロセスの中断となった。時間上限で終わった開発版はneeds_humanとして記録していた。新しい版では時間予算の終了をbudget_exhaustedと区別する。

この結果からハーネスの優位性は示されない。一方、原稿がないことから、通常のCodexの主張や引用が正しいと結論づけることもできない。人間による内容評価はpendingであり、品質採点を捏造していない。

## 見つかった実装上の問題と対応

- 探索済みの取得操作にもモデル選択を繰り返し、時間と利用量を消費した。探索計画とホスト順位から決まる取得操作は決定的に実行するよう変更した。
- 上位文献が入れ替わると累計取得予算を超え得た。累計の取得対象に予算を適用した。
- 初期検索の代表文献が引用経由の新しい文献の取得を妨げた。引用探索後の順位処理を修正し、統合テストを追加した。

これらの修正後の固定版で、同じ評価を再実施する必要がある。今回の実行をその検証済み結果と扱わない。

## 別途の実接続確認

既存の実文献に基づく草稿を複製したローカルDBで、事実検証と別モデル査読を確認した。これは6実行の比較に追加した成功例ではなく、ソフトウェア接続の検証である。

- 指示の曖昧さにより、文書ID・ハッシュによる対応を外部URL不足と取り違えた。一律の判断不能という誤った手順を修正した。
- 留保欄の文を本文として抽出した結果は一致検査が拒否した。本文と留保欄を明示する契約へ修正した。
- 修正後は38主張について、暫定支持29、部分的支持3、判断不能6を保存し、査読はreviseを返した。
- AIの支持判定は人間確認ではない。実モデル名のCLI確認はunconfirmedであり、同一提供元の共通の誤りも残る。

## 人間評価

匿名化した原稿と採点表を作成し、ユーザーが先行研究・論証・反論・限定条件・追跡可能性と事実の支持を評価する。原稿がない実行の品質は欠測として保持する。結果取り込み後に本資料を更新する。手順は[EVALUATION.md](EVALUATION.md)を参照。

## Narrow feasibility loop (2026-10-13)

A separate developmental study asked whether research-agent reports should
separate unverified claims from claims withdrawn after counterevidence. Requested
author/reviewer models were gpt-6.1-sol/gpt-6-astra with low effort. It is not an
additional comparative pilot run or evidence of superiority.

The run produced an English draft, received an independent-context review,
applied two accepted manuscript repairs without changing the selected thesis,
and reverified/reviewed each replacement. The first verification yielded 37
assertions (33 supported, 3 partial, 1 undetermined) and one incomplete block.
The final current manuscript yielded 23 supported assertions in the model's
assessment, with exact stored source/manuscript spans checked by the host.
These are AI assessments, not human-confirmed factual truth.

The final review still requested revision: factual claims about search counts,
reading scope and evidence status were excluded by the verifier as process
self-reports but treated by the reviewer as verifiable ledger assertions. After
two accepted repairs left that disagreement unresolved, the controller was
explicitly interrupted and the study handed off as `needs_human`. It was not
marked ready or finalized. The current draft, three reviews, original source
snapshots, failed repairs, replay provenance and interruption records remain in
the private database/export.

The exercise exposed and fixed total-search and abstract acquisition budget
bypasses, operational resumes invalidating manuscripts, coverage failures
unnecessarily discarding valid assertions, and missing draft-validation
feedback. Code changed during the exercise; one saved model output was
revalidated under the changed host coverage gate without another model call,
with identical scientific inputs and explicit replay metadata. Three full-text
sources and three extra abstracts had already been acquired before the budget
fix; the originally intended three-source limit was therefore not achieved.
Source hashes and all attempts are preserved. A future fixed-version run and
human grading are required before drawing quality or efficiency conclusions.

The loop now reaches drafting, review and manuscript revision in this case.
The next methodological decision is how to verify internal ledger claims
separately from literature-backed assertions, rather than repeating manuscript
rewrites or weakening the factual gate.

## Execution-ledger recheck (2026-10-13)

The previous final manuscript A017 was rechecked in a database copy without
rewriting it or discovering new literature. The original study was preserved.
The accepted report contains 22 AI-supported literature assertions and 19
execution-record assertions: 8 host-supported, 11 undetermined. A fresh review
still requested revision and the copied study remains `needs_human`.

Search count/provider and major reading-page checks now work, improving the
previous blanket exclusion. The reviewer still found missing metadata/evidence
status assertions and incomplete reading-to-paper mappings, and noted
inconsistent extraction of uncertainty statements as activity assertions.
Stored activity consistency does not prove actual external activity, and missing
record fields do not establish negative claims. No superiority or academic
approval is established.

The first attempt timed out at 300 seconds. A 600-second retry returned an
extraction containing one malformed expected-value string. Host contract 7 now
retains malformed/nonfinite values as undetermined without losing valid facts,
accepts equivalent record-root/envelope paths and dictionary-key membership.
That saved extraction was explicitly revalidated under identical scientific
inputs, followed by a fresh model review. Raw failed outputs, replay provenance
and the original study remain preserved privately. Code changed during the
recheck; this is diagnostic evidence, not a fixed-release evaluation.
