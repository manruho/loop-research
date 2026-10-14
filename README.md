# loop-research

Codexを実行基盤とする、ポジションペーパーのための研究支援ハーネス。

文献探索、主張の比較、根拠の追跡、批判的査読、研究方針の再設計を扱う研究環境を目指します。研究の正確性と、人間が判断を検証できることを重視します。

## 現在の状態

CLI研究ハーネスを実装しました。問いの整理 → 文献探索・取得・読解 → 複数の立場の比較 → 論証設計 → 英語草稿 → 事実主張ごとの検証 → 別モデル・別コンテキストでの査読 → 追加調査・主張の再設計を、SQLiteの保存状態から実行します。保留・撤回も研究結果として残します。

構想書、展望メモ、開発進捗ダッシュボードはローカル資料としてGitの管理対象から除外しています。研究用GUIは今後の段階です。

## まず使う

ChatGPT認証済みの公式Codex CLIとPython 3.10以上が必要です。新規の自律研究には執筆・査読モデルを明示し、異なるモデルを指定してください。利用できないモデルは別モデルへ自動置換しません。PDF抽出にはPopplerを使用します。

```bash
codex login
python3 -m loop_research codex status
python3 -m loop_research start "研究エージェントは主張の撤回を成果として明示すべきか" \
  --model gpt-6.1-sol --review-model gpt-6-astra --steps 20
python3 -m loop_research study status
python3 -m loop_research study next
```

既定DBは`.research/research.sqlite3`です。別研究は、コマンド先頭に`--db .research/別研究.sqlite3`を指定します。`start`は新規研究用です。既存研究は以下で続けます。

```bash
python3 -m loop_research study run --steps 20
python3 -m loop_research study guide "測定していない効果を事実として主張せず、価値判断と仮説を区別する"
python3 -m loop_research study resume --reason "未解決の反論を調べる" --steps 20
python3 -m loop_research task list
python3 -m loop_research artifact list
python3 -m loop_research issue list
python3 -m loop_research study export --output .research/export-01
```

作業上限は完成判定ではありません。`budget_exhausted`は上限到達、`needs_human`は判断または失敗への介入待ち、`held`は保留、`withdrawn`は撤回です。`ready_for_human`は人間の確認待ちで、学術的正しさを保証しません。根拠を手動確認し、現行版の草稿と査読、重大な未解決指摘がないことを確認した後に`study finalize --reason "確認内容"`を実行します。根拠の変更後は草稿と査読を更新する必要があります。

エクスポートには`draft.md`、編集用`draft-editable.json`、研究地図、判断履歴、査読、未解決事項、書誌、ハッシュ付きmanifestを含めます。原資料も必要なら`--include-sources`を付けます。出力先は新しいディレクトリにしてください。

```bash
python3 -m loop_research study import-draft --file .research/export-01/draft-editable.json --reason "人間が論証と限定条件を修正"
```

編集した草稿は新しい版として保存され、再査読が必要になります。`study pause`、`study select`、`claim fork`、`claim merge`、`issue respond`による介入もできます。引数は各コマンドの`--help`で確認できます。

## 研究上の境界

- 新規性の評価は取得・読解した範囲に限定します。検索結果や抄録を全文確認と扱いません。
- 読解は既定で各資料の一部ページです。`--paper-limit`、`--pages`、`--queries`で範囲を調整できます。OCR、図表・数式の理解、文献版の自動統合は未対応です。
- 引用は保存本文との一致を検査します。PDF抽出の誤りや研究解釈の正しさは人間による確認が必要です。
- AIは根拠を自動で`checked`にしません。別モデル・別コンテキストの査読でも、同じ提供元の共通の誤りは残り得ます。CLIが実モデル名を返さない場合は未確認として記録します。
- 問い・草稿・選択した本文抜粋をCodexへ渡します。検索語はarXiv/Crossref/OpenAlexへ送信します。ローカル取り込み資料も読解対象にすると抜粋がモデルに送られます。
- ChatGPT認証を利用し、APIキー認証に切り替えて課金する処理はありません。契約の利用制限や通信障害による停止は履歴に残します。

## 起動

Python 3.10以上が必要です。リポジトリのルートから、追加ライブラリなしで実行できます。

```bash
python3 -m loop_research --help
python3 -m loop_research init "AIの検証負担を評価すべきか"
python3 -m loop_research claim add "回答精度に加えて検証負担も評価すべきだ"
python3 -m loop_research status
```

`research`コマンドとして利用する場合は、仮想環境にインストールします。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/research --help
```

以下の例の`python3 -m loop_research`は、インストール後の`research`に置き換えられます。

## 文献探索・検証の改善

OpenAlexを追加し、引用と被引用の関係を追跡できます。キーなしでも収録範囲と無料枠の制限内で使用できます。必要なら`OPENALEX_API_KEY`を環境変数に設定してください。キーはDBやエクスポートに保存せず、Codex子プロセスにも渡しません。

```bash
python3 -m loop_research paper search "research provenance" --provider openalex --limit 10
python3 -m loop_research paper related P001 --direction both --limit 10
python3 -m loop_research paper graph
python3 -m loop_research paper add --openalex W4402952666
python3 -m loop_research study verify
```

新規研究の既定は検索6件、取得対象12文献、初回読解は各2ページです。自律引用探索は種文献3件・深さ1、各方向10件、追加文献30件まで。`--citation-seeds 0`で自律引用探索を止められます。上限、空の応答、失敗は探索履歴に残ります。OpenAlexが引用を収録していない場合もあり、空の結果を引用の不存在や網羅性の証明にしません。

草稿内の個々の事実を抽出し、保存原資料の文字範囲に対応づけます。判定は`支持 / 部分的支持 / 反証 / 判断不能`で、AIの暫定判断です。引用の一致はホスト側が検査しますが、意味上の支持と抽出漏れは査読と人間の確認が必要です。検証は`U...`成果物とエクスポートの`fact-verification.json`に保存します。未支持の事実や抽出不足は重大指摘として再設計へ戻します。

詳しい契約と制約は[研究品質の設計](QUALITY.md)、通常のCodexとの比較手順は[実証評価](EVALUATION.md)を参照してください。

## 根拠を登録する

以下は操作説明用の架空の資料です。実際の研究では、取得した資料の識別子・原文・箇所を記録してください。

```bash
python3 -m loop_research evidence add \
  --claim C001 \
  --source "操作例の資料（架空）" \
  --source-version "操作例 v1" \
  --locator "p. 3, Results" \
  --quote "Participants spent time checking the answers." \
  --interpretation "検証に時間を使った観測を支える。評価指標への導入は、この観測だけでは導けない。" \
  --relation qualifies \
  --level passage \
  --status unchecked
python3 -m loop_research evidence show E001
python3 -m loop_research claim show C001
```

確認範囲は`search`（検索結果）、`abstract`（抄録）、`passage`（本文の該当箇所）です。全文精読済みの状態は設けていません。関係は`supports`（支持）、`opposes`（反対）、`qualifies`（限定・留保）。いずれも入力者の解釈であり、自動判定ではありません。

根拠は既定で`unchecked`（未確認）。`checked`（指定範囲を手動確認済み）には原文と箇所が必要です。取得できない資料は`inaccessible`として残せます。版が不明な場合は`source_version`が空のまま残ります。`acquired_at`は登録日時であり、実際の文献公開日を意味しません。

支持関係をまだ評価していない資料には`unassessed`を指定できます。AIによるページ読解の入力は、この状態で登録します。

## 文献を探して取り込む

文献探索と取得は追加の推論APIやAPIキーなしで動きます。PDFの本文抽出にはPopplerの`pdftotext`が必要です。この環境には導入済みです。Ubuntuで未導入の場合：

```bash
sudo apt install poppler-utils
```

研究を初期化したフォルダで検索します。

```bash
python3 -m loop_research paper search "human AI collaboration" --provider crossref --limit 5
python3 -m loop_research paper search "The AI Scientist" --provider arxiv --limit 5
python3 -m loop_research paper list
python3 -m loop_research search list
python3 -m loop_research search show S001
```

検索先の既定はCrossrefです。CrossrefはDOIに基づく書誌、arXivはプレプリントを中心とする検索です。arXivの通常の検索語は全フィールドのフレーズ検索です。明示したAND/OR/ANDNOT、括弧、フィールド指定も扱います。日本語のテーマでも検索できますが、各検索先に収録された文献の用語に合わせた英語の語句が必要になることがあります。

検索語、検索先、件数上限、問い合わせURL、応答、発見順位、日時を保存します。同じ検索先の同じarXiv版・DOIは重複登録せず、複数の発見経路を残します。arXiv版と出版版など、別の識別子を持つ資料の統合は行いません。検索件数の上限は20件で、網羅性や新規性の証明ではありません。失敗と中断も検索履歴に残します。[arXiv API資料](https://info.arxiv.org/help/api/user-manual.html)、[Crossref API資料](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)

arXiv APIはローカルCLI間で呼び出しを直列化し、少なくとも3秒の間隔を設けます。複数マシンにまたがる制御は行いません。[arXiv API利用条件](https://info.arxiv.org/help/api/tou.html)

### PDF・抄録・ローカル資料

一覧から文献IDを選び、資料を取り込みます。

```bash
python3 -m loop_research paper show P001
python3 -m loop_research paper fetch P001
python3 -m loop_research document list
python3 -m loop_research document show D001 --page 1
```

arXivにはPDFの取得先を結び付けます。CrossrefではPDFリンクが提供されていないことがあります。取得先が不明な場合は失敗を記録し、本文を読んだとは扱いません。出版社のHTMLページやアクセス拒否ページを論文本文として取り込むこともありません。

入手できる資料や、メタデータ上の抄録を指定する場合：

```bash
python3 -m loop_research paper fetch P001 --url "https://example.org/paper.pdf"
python3 -m loop_research paper fetch P001 --file /path/to/paper.pdf
python3 -m loop_research paper fetch P001 --file /path/to/paper.txt
python3 -m loop_research paper fetch P001 --abstract
```

`--abstract`は検索時の抄録を保存します。本文とは別の資料で、根拠の確認範囲も`abstract`になります。ローカル資料はPDFとUTF-8の`.txt`・`.md`に対応します。書誌との対応やローカル資料の版は利用者による指定として扱います。

検索で見つからなくても、既知の文献を登録できます。

```bash
python3 -m loop_research paper add --arxiv 2408.06292v3 --title "The AI Scientist"
python3 -m loop_research paper add --title "文献のタイトル" --url "https://example.org/paper" \
  --pdf-url "https://example.org/paper.pdf" --source-version "入手した版"
```

手動登録は書誌の確認を意味しません。arXivで版を省略した場合や出版版の詳細が不明な場合は、版は不明のまま保存します。

原資料のバイト列、SHA-256、取得先、リダイレクト後の取得元、版、日時、抽出方法、ページごとの本文をSQLiteへ保存します。PDFのページ番号は紙面上の印字ではなく、ファイルの先頭から数える物理ページ番号です。テキスト・抄録は単位1として扱います。抽出本文は空白を正規化し、単語や句読点の書き換えはしません。

資料は最大20MB、PDFの本文抽出は最大45秒です。画像だけのPDF、暗号化・破損などで抽出できないPDFは`extraction_failed`として原資料と失敗理由を残します。依存ツールを導入した後などに同じ資料を再取得すると、再抽出できます。OCRは未対応です。図表、数式、段組みなどの抽出誤りは原資料との照合が必要であり、`extracted`は精読済みを意味しません。

保存した原資料を確認するために出力できます。既存ファイルは上書きしません。

```bash
python3 -m loop_research document export D001 --output .research/source.pdf
```

抄録資料の原資料は検索元のメタデータJSONなので、出力先は`.json`にしてください。

### 本文を主張と結び付けて読解する

保存した本文から語句を探せます。大文字小文字を区別しないフレーズ検索です。意味検索ではなく、未発見を文献内の不存在の証明として扱いません。

```bash
python3 -m loop_research document find "human" --document D001
```

まずページ全体をCodexへ渡す場合、原文をコマンドへコピーする必要はありません。

```bash
python3 -m loop_research document review D001 --claim C001 --page 1
```

この操作は指定ページを`unassessed`・`unchecked`の根拠候補として登録してから、Codexに主張との関係を評価させます。`--model`と`--timeout`も指定できます。読解対象は選択した一ページであり、全文精読とは扱いません。推論に失敗した場合も入力の根拠候補と実行記録は残り、`runs`から確認・再依頼できます。ページが空、または30,000文字を超える場合は拒否します。

必要な箇所だけを根拠として登録する場合：

```bash
python3 -m loop_research evidence from-document \
  --claim C001 --document D001 --page 1 \
  --quote "保存本文から選んだ原文" \
  --interpretation "この箇所と主張の関係。未確認の点も記す。" \
  --relation qualifies
python3 -m loop_research evidence review E001
```

引用は空白を正規化した該当ページの本文に一致する必要があります。出典、版、ページ、原資料のハッシュを根拠と結び付け、Codexが返した抜粋も入力原文との一致を確認します。これは取得資料の抽出本文との機械的な照合であり、主張の学術的な妥当性や出典の真正性を保証するものではありません。

## 問題の記録と主張の改訂

```bash
python3 -m loop_research evidence flag E001 \
  --reason "原文の検証時間は別の作業を含んでおり、解釈が強すぎる"
python3 -m loop_research status
python3 -m loop_research claim revise C001 \
  "AI評価では、検証作業の負担を独立した検討課題にすべきだ" \
  --reason "観測から評価指標の必須化へ進む論証が不足しているため限定"
python3 -m loop_research claim show C001
python3 -m loop_research history --claim C001
```

根拠の追加・確認変更・問題の指摘は、通常の候補を`needs_review`（再確認が必要）にします。保留・撤回中の主張は、その状態を維持します。履歴には根拠の変更と主張の状態を残します。

主張を改訂すると版が増え、状態は`needs_review`になります。過去の主張と根拠は残り、古い版に対応する根拠は`applies_to_current_revision: false`と表示されます。新版への根拠の再登録には、支持関係の再検討が必要です。

根拠そのものの確認をやり直す場合：

```bash
python3 -m loop_research evidence assess E001 --status checked \
  --reason "登録した原文と箇所を再確認し、限定的な観測として妥当と判断"
```

原文や解釈を変える場合は、古い記録を残して新しい根拠を登録します。根拠の再確認だけで主張の状態は自動的に解決されません。

研究判断は理由とともに記録します。

```bash
python3 -m loop_research claim decide C001 --status candidate \
  --reason "限定後の主張として検討を続ける。追加根拠は未確認"
python3 -m loop_research claim decide C001 --status held \
  --reason "関連領域の調査が足りないため保留"
python3 -m loop_research claim decide C001 --status withdrawn \
  --reason "競合研究との差分を説明できないため撤回"
```

`candidate`は研究候補であり、採択・正しさ・新規性の保証ではありません。現行版に`disputed`（問題あり）や`inaccessible`の根拠が残っている場合は、候補への復帰を拒否します。`unchecked`の根拠は未確認のまま表示されます。

撤回済みの主張も、明示的な判断や改訂で再検討できます。履歴は消えません。

## 保存と再開

既定の保存先は、実行ディレクトリ内の`.research/research.sqlite3`です。同じディレクトリで`status`や`history`を実行すると再開できます。既存のDBに対する`init`は拒否します。

別の研究や任意の保存先を使う場合は、サブコマンドの前に`--db`を指定します。

```bash
python3 -m loop_research --db .research/another.sqlite3 init "別の問い"
python3 -m loop_research --db .research/another.sqlite3 status
```

一つのDBで一つの研究を管理します。変更と履歴は同じトランザクションで保存します。出力はJSON、操作エラーは標準エラー出力と終了コード1で返します。引数の不備は終了コード2です。

研究DBとローカル資料は`.gitignore`で除外されています。バックアップが必要な場合はCLIを終了してからSQLiteファイルをコピーしてください。

## ChatGPT認証のCodexによる根拠確認

公式Codex CLIが必要です。Codex CLI 0.162.1で実接続を確認しています。`--ignore-user-config`、`--ephemeral`、`--output-schema`に対応するCLIを使ってください。

```bash
codex login
python3 -m loop_research codex status
python3 -m loop_research evidence review E001
python3 -m loop_research runs
python3 -m loop_research run show R001
```

`codex status`は研究DBなしで実行できます。ChatGPT認証を確認しますが、契約内容や利用枠の残量を確認する機能ではありません。未認証やAPIキー認証では実行を拒否します。ログイン済みなら`codex login`の再実行は不要です。

このアダプターは、保存されたChatGPT認証を使う`codex exec`を呼び出します。子プロセスから`OPENAI_API_KEY`と`CODEX_API_KEY`を除き、OpenAIプロバイダーとChatGPT認証を指定します。API課金へのフォールバックは行いません。利用には契約のCodex利用制限が適用されます。[公式認証資料](https://learn.chatgpt.com/docs/auth)

依頼するのは、**登録された原文と主張の関係の評価**です。取得資料を読む場合も、指定ページ・箇所を対象とし、全文精読や出典の真正性の検証とは扱いません。原文と原文箇所が未入力、資料が取得不能、根拠が旧版の主張に対応している場合は、推論を開始せずエラーにします。原文は30,000文字以内に限定します。

Codexには、主張、原文、出典、版、原文箇所、確認範囲、以前の解釈を渡します。渡した資料はOpenAI側へ送信されます。研究DB全体は渡しません。一時ディレクトリでread-onlyモードを使い、ウェブ検索とユーザー設定の読み込みを無効にします。プロジェクト設定を継承しないため、モデルを指定する場合は`--model`を使ってください。

```bash
python3 -m loop_research evidence review E001 --model gpt-6.1-sol --timeout 180
```

モデル省略時はCodex CLI組み込みの既定モデルです。アカウントで利用できないモデルを指定した場合は、失敗として記録します。

結果には次の項目が含まれます。

| 項目 | 意味 |
| --- | --- |
| `verdict` | `supports`（支持）、`opposes`（反対）、`qualifies`（限定が必要）、`insufficient`（根拠不足）、`unassessable`（判断不能） |
| `explanation` | 判定理由 |
| `supporting_excerpt` | 登録原文からの抜粋。原文にない引用は拒否 |
| `limitations` | 資料の範囲や未確認事項 |
| `suggested_claim` | 限定案があれば記録。元の主張は自動変更しない |
| `execution` | CLIバージョン、スレッドID、CLIが返すトークン利用情報 |

AI評価は根拠に対応する実行記録として保存します。`evidence show E001`の`ai_reviews`から実行IDを確認し、`run show R001`で入力スナップショットと評価結果を辿れます。AI評価によって、根拠の手動確認状態`checked`や元の解釈・関係を書き換えることはありません。主張は再確認へ戻し、保留・撤回中ならその状態を維持します。

### 失敗・中断と再実行

実行開始時に、対象の主張の版・根拠・入力スナップショット・実行条件を保存します。正常終了と結果の形式・引用の一致を確認できた場合だけ、暫定評価として取り込みます。[公式の非対話実行資料](https://learn.chatgpt.com/docs/non-interactive-mode)

- `completed`：結果を保存。学術的な正しさの保証ではない。
- `failed`：認証、CLI、通信、出力形式、引用などの問題。失敗理由を記録。
- `timed_out`：制限時間を超え、Codex子プロセスを停止。
- `interrupted`：Ctrl+Cによる中断、または手動で中断記録を確定。
- `stale`：処理中に主張や根拠が変更された。結果を残すが現行状態へ適用しない。
- `running`：未終了。強制終了した場合にもこの状態で残ることがある。

`review`の既定の推論制限は180秒です。認証とCLI機能の確認には、それぞれ別の短い制限時間を設けています。エラー時は実行IDを標準エラーへ表示するので、`run show`で確認してください。再実行は新しい実行IDとして記録し、過去の失敗は残します。CLIの認証ログや内部推論は研究台帳に保存しません。

強制終了などで`running`のまま残った記録は、**関連するCodexプロセスが停止したことを確認してから**閉じます。このコマンドはプロセスを停止しません。

```bash
python3 -m loop_research run abandon R001 --reason "関連プロセスを停止済み"
```

既存DBの版1〜6は、次に開く際に版7へ移行します。旧版の確認待ち・確定済み状態は新しい検証工程が未実施のため再確認待ちに戻し、過去の判断は履歴に保持します。原資料と抽出器の組み合わせごとに本文の版を保持し、過去の引用を新しい抽出結果で置き換えません。移行中の外部キー整合性も検査します。

## アーキテクチャ

外側のハーネスが問い、主張の版、根拠、課題、実行履歴、次の工程を管理します。内側のCodexは保存した入力と厳格な出力形式を受け、限定された工程を実行します。結果は形式・引用・研究状態の一致を検査してから適用します。遅れて届いた結果は、変更済みの研究状態へ適用しません。

`workflow.py`が研究ループ、`workflow_store.py`が判断と版、`roles.py`が工程契約、`engine.py`がCodex実行、`literature*.py`が資料取得と保存、`export.py`が成果物出力を担当します。実行はLinuxで確認しています。GUIはこの研究操作を利用する形で追加できます。

確認用の架空資料による統合テスト、旧DB移行、引用拒否、重大指摘の再評価、中断と所有プロセスの停止を`tests/`に収録しています。これらはソフトウェアの動作確認で、研究成果の妥当性評価ではありません。

### 実行記録の照合と修正ループの停止

`study verify` は文献の事実主張（F ID）に加え、検索回数・読解ページ・
根拠の状態などの記述（G ID）を保存済みの実行記録と照合します。
`study status` に `ledger_verdicts`、エクスポートに
`ledger-verification.json` が追加されます。記録との一致は、外部文献の
正しさや研究の網羅性を証明するものではありません。

再設計の応答対象は、現行査読で新設された未解決指摘と、その査読が `persists` と再評価した過去の未解決指摘です。対象IDは入力の `issues` / `allowed_issue_ids` に保存します。解決済み・却下済み・適用外の指摘や、現行査読で引き継がれていない指摘への応答は拒否します。応答は修正提案であり、解決判定には次の査読が必要です。入力の範囲が変わるため、旧方式の再設計結果は再利用しません。

同じ重大な査読指摘が2回の草稿修正後も残ると、自動修正を止めて
`needs_human` に移ります。未解決の指摘と草稿・検証・査読のIDを履歴に残します。
詳しくは [QUALITY.md](QUALITY.md) を参照してください。

分類の回帰評価は `research eval extraction --model gpt-6-astra --output .research/extraction-eval`
で実行できます。開発者が作成した短い評価例への一致を測るもので、
人間による独立評価や論文全体の抽出精度の証明ではありません。

### LaTeXとPDFで出力

```bash
research --db .research/study.sqlite3 study export --output .research/paper-export
research --db .research/study.sqlite3 study export --output .research/paper-pdf --pdf
```

通常のエクスポートにも `paper.tex`、`references.bib`、`latex-trace.json` が含まれます。
`--pdf`を付けるとLuaLaTeX・BibTeXで `paper.pdf` も生成します。
必要なものはLuaLaTeX、BibTeX、luatexja、natbib、geometry、hyperrefです。
日本語を含む留保も扱える汎用article形式で、投稿先のテンプレートへ変更できます。
手動コンパイル手順は生成された `LATEX.md` にあります。

本文は安全な文字列としてエスケープし、引用キーは保存済みの文献・根拠から生成します。
本文のraw LaTeXコマンド・数式としての解釈には対応していません。
各段落の主張・根拠IDとTeX行の対応を `latex-trace.json` に残します。
PDF生成はshell escapeを無効にした一時作業ディレクトリで行い、
文字欠落や最終パスの未解決引用がある場合は失敗として扱います。

LaTeXの直接編集は研究DBへ反映されず、検証済み扱いにもなりません。
研究上の修正は構造化草稿へ戻して再検証・再査読し、再エクスポートしてください。
既存の出力先は上書きしません。Markdownと追跡用JSONも引き続き出力します。


### 文献の事実と研究記録の事実

草稿の `fact` は学術資料の事実で、文献の `evidence_ids` を必要とします。検索件数・読解範囲・根拠の確認状態など、保存された研究記録の事実は `ledger_fact` とし、文献引用は付けません。草稿入力にもハッシュ付き `ledger_snapshot` を渡します。検証時に個々の主張の原稿範囲を `G...` として抽出し、台帳のパス・比較式・実際の値・スナップショットSHAへ対応付けます。抽出漏れ・未知のパス・不一致は完了を阻止します。ラベルや一致する数値だけでは文章の意味上の支持を保証せず、査読と人間の確認が必要です。「記録がない」ことから「活動がなかった」とは推論しません。文献と台帳の事実が混在する段落は分けてください。従来の草稿は履歴に残り、検証契約変更後は再検証が必要です。

初回草稿生成の失敗も、同じ研究basisと論証IDに限って `validation_feedback` として次回入力へ渡します。失敗した出力は適用せず保存し、再試行には通常の明示的な再開を使います。

### 探索候補の選別と識別子解決

語彙選別（`--semantic-selection 0`）では、自動取得候補と引用探索seedは、問い・探索方針の語との一致で絞ります。一般的なrisk/evaluation等だけの一致や、Crossrefのcomponentを自動取得候補にしません。これは限定的な英語の語彙ヒューリスティックで、意味上の関連性や探索の網羅性を保証しません。重要な文献が漏れる場合は手動登録で追加できます。候補の元記録・検索結果・既存資料は削除しません。手動登録資料は候補として保持し、引用探索seedでは優先します。

OpenAlexのDOI解決は `doi:` の識別子形式を使い、応答のDOIまたは所在のDOIを照合します。arXiv文献は候補検索の上位5件から、所在URLのarXiv IDが登録IDと一致する一意の候補だけを採用します。タイトル類似だけでは統合せず、欠落・曖昧な対応は失敗として記録します。未収録DOIや補助ファイルへの404を、別論文への推測で埋めません。[OpenAlexの識別子仕様](https://help.openalex.org/data/works/dois/)を参照してください。


## 限られた時間での暫定草稿と意味的選別

初回は最大2検索試行から取得・読解へ進み、残りの検索を保留します。取得できる根拠がない場合は残りの検索へ戻ります。既定では同じ研究課題の読解2件後、または残り時間が4工程分のtimeoutを下回り少なくとも1件の読解と取得根拠がある場合に、追加の探索・取得・読解を保留して論証・暫定草稿・検証・査読を優先します。探索予算は消費済みにせず、保留した作業は暫定査読後に再開します。暫定草稿は履歴に残りますが、追加根拠や範囲変更後には現行性を再判定します。検証・査読・手動確定の条件は従来と同じです。時間不足や未支持の主張は完成扱いしません。

`--draft-checkpoint-readings 0` はこの区切りを無効にします。`--semantic-selection 0` は従来の語彙選別を使います。既定の意味的選別では候補の書誌情報と取得文書の選別用抜粋をまとめて評価し、中心研究・反証研究・周辺研究・不確か・関連なしを理由付きでY成果物に保存します。中心研究と反証研究を交互に優先します。人間が登録したseedは保持します。

選別用の抜粋を見たことはL読解記録やE根拠の取得には数えません。モデルの選別判断は暫定的で、意味的な関連性や重要研究の網羅性を保証しません。1回に最大60候補、長い資料は `--selection-page-limit`（既定16、最大60）で均等に選んだページの冒頭・末尾各600文字を扱います。この境界外の内容・候補は未確認であり、モデル呼び出しの時間と使用量も予算に含まれます。取得済み資料と失敗履歴を消しません。選別キャッシュは問い・人間の指示・候補/資料SHA・指定モデル/effortが一致するときだけ再利用します。

検証契約11では段落に加えて、タイトルMT001、見出しMH...、不確実性欄MU...を対象にします。文献/台帳の抽出範囲が対象全体と一致しなければ結果を適用しません。判定には抽出箇所の元の文脈も保存し、旧数値を否定する文から数値だけを切り出す誤りや限定条件の脱落を査読できるようにします。文脈と原文一致の保存は意味上の支持の証明ではありません。LaTeXの追跡ファイルにもこれらの対象と行番号を保存します。

## 比較・除去実験と固定候補評価

### 時間制限付き実行の配分

配分の設計と実測結果は[検証時間の予約の測定記録](benchmarks/validation-reserve-results.md)を参照してください。

`study run --seconds` と `eval run --seconds` では、事実検証に実行予算の30%、査読に15%を予約します（各予約は設定済みの1工程のtimeout以下）。前工程の推論timeoutは、この予約を差し引いた残り時間に制限し、検証は査読分を残します。予約を使い切って前工程を延長することはありません。モデルが制限内に完了する保証ではなく、足りなければ `budget_exhausted` として再開可能な状態で停止します。検索・取得の通信遅延も残り時間を消費します。

時間制限付きでは、分析メモを簡潔な英語にし、競合案を2案、論証メモを3節、草稿を600〜900語程度にまとめる作業用プロンプトを使います。検証・査読のプロンプトと合格条件は維持し、すべての必須節・最も強い反論・限定条件・根拠追跡を残します。現行草稿があれば追加探索より検証と独立した査読を優先します。草稿が短くても完成や学術的承認とは扱いません。

作業の `config.run_budget` にポリシー、全予算、開始時の残り時間、予約時間、工程上限を保存します。経過秒数はキャッシュキーに入れないため、時計が進むだけで再推論しません。再開には新しい時間予算を使い、過去の草稿・失敗履歴を保持します。時間制限を付けない実行や個別の手動工程にはこの配分・短縮プロンプトを適用しません。

検証結果の形式や引用位置が契約違反で拒否された場合、同じ入力に対する失敗理由を渡し、時間制限付きのループ内で一度だけ訂正を依頼します。草稿・根拠・検証条件は変更せず、失敗した応答も保存します。予約時間が足りなければ訂正を始めず、再度の違反は `needs_human` で停止します。通信・認証・タイムアウトの再試行や、受理済みの未支持判定を都合よく得直すための再試行は行いません。

```bash
loop-research eval run --output .research/paired-v1 \
  --model gpt-6.1-sol --review-model gpt-6-sol --seconds 1800 \
  --methods baseline harness harness-no-checkpoint harness-lexical harness-no-citations
loop-research eval packet --output .research/paired-v1 --review-model gpt-6-sol
loop-research eval selection --output .research/selection-v1 --model gpt-6.1-sol --k 2
```

各除去方式は早期草稿・意味的選別・引用追跡のいずれかだけを無効にし、根拠検証や査読は無効にしません。実装SHA、課題SHA、方式、モデル、予算を固定して再開条件を検査します。工程別の呼び出し数・時間・失敗、最初の草稿までの時間/呼び出し数、CLIが報告した使用量、不明な使用量の件数を記録します。参照集合について発見・読解・最終引用を区別します。未完成・保留・失敗も評価対象に残し、人間の品質採点は別に取り込みます。3課題各1回の比較だけでは統計的優位性を主張できず、モデルや探索ツールの違いも交絡として残ります。

固定候補評価は `benchmarks/selection-cases.json` の人工候補と開発者ラベルを使います。モデルにラベルは渡さず、同じ候補集合で語彙選別と意味的選別のprecision/recall@kおよび反証候補recall@kを比較します。保存ランキングを採点する場合は `--model` の代わりに `--ranking ranking.json` を指定します。評価は候補集合内の選別の診断であり、実文献探索の再現率、独立した専門家の正解、研究品質の優位性を示すものではありません。


### 実文献の短時間比較

実行済みの比較結果・失敗・修正後の再試行・未評価項目は[実文献パイロットの測定記録](benchmarks/benchmark-reporting-results.md)を参照してください。

`benchmarks/benchmark-reporting-pilot.json` は、前回の研究課題を1件だけ使う事前定義の比較です。HELMだけを共通の開始資料とし、7本の参照集合はモデルへの正解リストとして渡しません。評価コマンドは1〜20課題に対応し、重複した問いを水増しして実行する必要はありません。

```bash
loop-research eval run --output .research/reporting-pilot-new \
  --cases benchmarks/benchmark-reporting-pilot.json \
  --model gpt-6.1-sol --review-model gpt-6-sol --seconds 600 \
  --methods baseline harness harness-no-checkpoint
loop-research eval selection --output .research/reporting-selection-new \
  --cases benchmarks/benchmark-reporting-selection.json --model gpt-6.1-sol --k 5
```

参照集合・選別ラベルは一次資料の抄録を調べたアシスタントの暫定判断で、網羅的・独立した専門家正解ではありません。反証側の研究は透明性等の効果に対する留保であり、この規範提案そのものを実証的に否定した研究ではありません。参照集合外の有力文献を見つけても不正解とはしません。

10分の比較で、引用拡張による再選別の繰り返しが遅延要因になりました。このため、初期草稿前に取得・読解可能な候補があれば引用拡張を保留し、一般の実行順も取得を引用追跡より先にします。選別理由は短い1文で記録します。長い資料の選別ページ数を減らすことで、未確認範囲が増える可能性は残ります。元の試験の結果と修正後の確認は分けて保存します。

台帳の `not_equals` は、抽出文中に明示的な比較表現がある既知・同型のスカラーに限って使います。欠落した記録、null、型の不一致、肯定文から切り出した断片は判断不能のままです。これは記録の値の比較であり、活動の不存在や文章の意味上の支持を証明するものではありません。文献の検証条件や最終的な人間の判断は省略しません。
