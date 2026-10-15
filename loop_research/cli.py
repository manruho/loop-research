"""Small CLI adapter; research behavior lives in store.py."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .store import ResearchError, ResearchStore
from .codex import codex_status, review_evidence, validate_review_options
from .literature import fetch_paper, register_paper, search_papers
from .workflow import ResearchWorkflow, study_lock
from .export import export_study


def study_options(command, steps=False):
    if steps:
        command.add_argument("--seconds", type=float, help="今回の実行時間上限（秒）。未完了の作業は保存して再開可能")
        command.add_argument("--steps", type=int, default=20, help="今回の作業上限。到達は研究完成を意味しない")
    command.add_argument("--model")
    command.add_argument("--review-model")
    command.add_argument("--review-effort", choices=["low", "medium", "high", "xhigh", "max", "ultra"])
    command.add_argument("--citation-seeds", type=int)
    command.add_argument("--citation-limit", type=int)
    command.add_argument("--citation-budget", type=int)
    command.add_argument("--timeout", type=float)
    command.add_argument("--effort", dest="reasoning_effort", choices=["low", "medium", "high", "xhigh", "max", "ultra"])
    command.add_argument("--provider", choices=["arxiv", "crossref", "openalex"])
    command.add_argument("--paper-limit", type=int)
    command.add_argument("--pages", type=int, dest="pages_per_document")
    command.add_argument("--search-budget", type=int, help="追加・代替検索を含む通算検索上限（引用追跡は別予算）")
    command.add_argument("--queries", type=int, dest="query_limit")
    command.add_argument('--draft-checkpoint-readings', type=int, help='暫定草稿に進む読解件数。0は従来の探索優先')
    command.add_argument('--semantic-selection', type=int, choices=[0, 1])
    command.add_argument('--selection-page-limit', type=int, help='選別用に均等抽出するページ数。1〜60、既定16')


def configure_options(store, args):
    settings = {key: getattr(args, key) for key in (
        "model", "review_model", "review_effort", "citation_seeds", "citation_limit", "citation_budget", "timeout", "reasoning_effort", "provider", "paper_limit", "pages_per_document", "query_limit", "search_budget", 'draft_checkpoint_readings', 'semantic_selection', 'selection_page_limit'
    ) if getattr(args, key, None) is not None}
    if settings:
        store.configure_study(getattr(args, "reason", None) or "CLI実行時の設定", settings=settings)


def parser():
    root = argparse.ArgumentParser(description="主張・根拠・改訂履歴を保存する研究CLI")
    root.add_argument("--db", default=".research/research.sqlite3", help="研究DB（既定: .research/research.sqlite3）")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="新しい研究を開始")
    init.add_argument("title")
    start = commands.add_parser("start", help="研究を新規作成し、文献探索から研究ループを開始")
    start.add_argument("title")
    start.add_argument("--audience")
    start.add_argument("--venue")
    study_options(start, steps=True)
    commands.add_parser("status", help="現在の研究状態と未確認事項")
    history = commands.add_parser("history", help="判断と変更の履歴")
    history.add_argument("--claim")
    codex = commands.add_parser("codex", help="ChatGPT認証の確認")
    codex.add_subparsers(dest="action", required=True).add_parser("status")
    evaluation = commands.add_parser("eval", help="公開課題での探索的比較と人間評価")
    actions = evaluation.add_subparsers(dest="action", required=True)
    retrieval = actions.add_parser('selection')
    retrieval.add_argument('--output', required=True)
    retrieval.add_argument('--cases', default='benchmarks/selection-cases.json')
    retrieval.add_argument('--model')
    retrieval.add_argument('--ranking')
    retrieval.add_argument('--k', type=int, default=2)
    retrieval.add_argument('--timeout', type=float, default=180)
    extraction = actions.add_parser("extraction")
    extraction.add_argument("--output", required=True)
    extraction.add_argument("--model", required=True)
    extraction.add_argument("--cases", default="benchmarks/extraction-cases.json")
    extraction.add_argument("--timeout", type=float, default=300)
    regression = actions.add_parser("verification-regression")
    regression.add_argument("--output", required=True)
    source = regression.add_mutually_exclusive_group(required=True)
    source.add_argument("--model")
    source.add_argument("--result")
    regression.add_argument("--cases", default="benchmarks/verification-cases.json")
    regression.add_argument("--timeout", type=float, default=300)
    evaluate = actions.add_parser("run")
    evaluate.add_argument("--output", required=True)
    evaluate.add_argument("--cases", default="benchmarks/position-papers.json")
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--review-model", required=True)
    evaluate.add_argument("--seconds", type=int, default=1800)
    evaluate.add_argument("--resume", action="store_true")
    evaluate.add_argument('--methods', nargs='+', choices=['baseline', 'harness', 'harness-no-checkpoint', 'harness-lexical', 'harness-no-citations'])
    packet = actions.add_parser("packet")
    packet.add_argument("--output", required=True)
    packet.add_argument("--review-model", required=True)
    scores = actions.add_parser("import-scores")
    scores.add_argument("--output", required=True)
    scores.add_argument("--file", required=True)
    report = actions.add_parser("report")
    report.add_argument("--output", required=True)
    commands.add_parser("runs", help="Codex実行の一覧")
    run = commands.add_parser("run", help="実行結果と入力の確認")
    actions = run.add_subparsers(dest="action", required=True)
    show = actions.add_parser("show")
    show.add_argument("id")
    abandon = actions.add_parser("abandon", help="プロセス停止後に残ったrunning記録を中断として閉じる")
    abandon.add_argument("id")
    abandon.add_argument("--reason", required=True)

    study = commands.add_parser("study", help="研究全体の実行・介入・成果物出力")
    actions = study.add_subparsers(dest="action", required=True)
    actions.add_parser("status")
    actions.add_parser("next")
    run = actions.add_parser("run", help="保存状態から自律実行")
    study_options(run, steps=True)
    resume = actions.add_parser("resume", help="保留・中断・上限到達後の研究を明示的に再開")
    resume.add_argument("--reason", required=True)
    study_options(resume, steps=True)
    configure = actions.add_parser("configure")
    configure.add_argument("--question")
    configure.add_argument("--audience")
    configure.add_argument("--venue")
    configure.add_argument("--reason", required=True)
    study_options(configure)
    guidance = actions.add_parser("guide", help="研究判断・価値判断・方針について人間の指示を残す")
    guidance.add_argument("text")
    select = actions.add_parser("select")
    select.add_argument("id")
    select.add_argument("--reason", required=True)
    pause = actions.add_parser("pause")
    pause.add_argument("--reason", required=True)
    finalize = actions.add_parser("finalize")
    finalize.add_argument("--reason", required=True)
    export = actions.add_parser("export")
    export.add_argument("--output", required=True)
    export.add_argument("--include-sources", action="store_true")
    export.add_argument("--pdf", action="store_true", help="LuaLaTeXとBibTeXでpaper.pdfも生成")
    imported = actions.add_parser("import-draft", help="人間が編集した構造化草稿を新しい版として取り込む")
    imported.add_argument("--file", required=True)
    imported.add_argument("--reason", required=True)
    for name in ("orient", "explore", "compare", "outline", "draft", "revise-draft", "verify", "review", "redesign", "read"):
        role = actions.add_parser(name, help="この工程だけを実行")
        study_options(role)
        role.add_argument("--force", action="store_true", help="同じ入力でも新しい評価を依頼")
        if name == "read":
            role.add_argument("id")
            role.add_argument("--page", required=True, type=int)

    task = commands.add_parser("task", help="研究作業の入力・結果・中断を確認")
    actions = task.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    show = actions.add_parser("show")
    show.add_argument("id")
    cancel = actions.add_parser("cancel")
    cancel.add_argument("id")
    cancel.add_argument("--reason", required=True)
    artifact = commands.add_parser("artifact", help="比較・論証・草稿・査読の版を確認")
    actions = artifact.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    show = actions.add_parser("show")
    show.add_argument("id")
    issue = commands.add_parser("issue", help="査読指摘と人間の応答")
    actions = issue.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    respond = actions.add_parser("respond")
    respond.add_argument("id")
    respond.add_argument("--status", choices=["addressed", "deferred", "rejected"], required=True)
    respond.add_argument("--reason", required=True)
    respond.add_argument("--uncertainty", required=True)

    paper = commands.add_parser("paper", help="文献の探索・取得・記録")
    actions = paper.add_subparsers(dest="action", required=True)
    search = actions.add_parser("search")
    search.add_argument("query")
    search.add_argument("--provider", choices=["crossref", "arxiv", "openalex"], default="crossref")
    search.add_argument("--limit", type=int, default=5)
    actions.add_parser("list")
    actions.add_parser("graph")
    related = actions.add_parser("related")
    related.add_argument("id")
    related.add_argument("--direction", choices=["references", "citations", "both"], default="both")
    related.add_argument("--limit", type=int, default=10)
    show = actions.add_parser("show")
    show.add_argument("id")
    add = actions.add_parser("add", help="既知の文献を登録。書誌の自動確認はしない")
    add.add_argument("--openalex", help="OpenAlex ID (W...)。書誌をAPIから取得")
    add.add_argument("--arxiv", help="arXiv ID。版の指定を推奨")
    add.add_argument("--title")
    add.add_argument("--url")
    add.add_argument("--pdf-url")
    add.add_argument("--source-version", default="")
    fetch = actions.add_parser("fetch")
    fetch.add_argument("id")
    location = fetch.add_mutually_exclusive_group()
    location.add_argument("--url", help="PDFの取得先URL")
    location.add_argument("--file", help="ローカルのPDF・UTF-8テキスト・Markdown")
    location.add_argument("--abstract", action="store_true", help="検索時の抄録を本文とは別の資料として保存")

    search = commands.add_parser("search", help="探索範囲と検索履歴")
    actions = search.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    show = actions.add_parser("show")
    show.add_argument("id")

    document = commands.add_parser("document", help="取得資料と本文箇所の確認")
    actions = document.add_subparsers(dest="action", required=True)
    actions.add_parser("list")
    show = actions.add_parser("show")
    show.add_argument("id")
    show.add_argument("--page", type=int)
    find = actions.add_parser("find", help="保存本文の語句検索。結果の抜粋を根拠に登録できる")
    find.add_argument("query")
    find.add_argument("--document")
    find.add_argument("--limit", type=int, default=5)
    export = actions.add_parser("export", help="保存した原資料をファイルへ出力。既存ファイルは上書きしない")
    export.add_argument("id")
    export.add_argument("--output", required=True)
    review = actions.add_parser("review", help="指定ページを根拠候補として登録し、Codexに読解を依頼")
    review.add_argument("id")
    review.add_argument("--claim", required=True)
    review.add_argument("--page", type=int, required=True)
    review.add_argument("--timeout", type=float, default=180)
    review.add_argument("--model")

    claim = commands.add_parser("claim", help="主張の登録・改訂・判断")
    actions = claim.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add")
    add.add_argument("text")
    show = actions.add_parser("show")
    show.add_argument("id")
    revise = actions.add_parser("revise")
    revise.add_argument("id")
    revise.add_argument("text")
    revise.add_argument("--reason", required=True)
    decide = actions.add_parser("decide")
    decide.add_argument("id")
    decide.add_argument("--status", choices=["candidate", "held", "withdrawn"], required=True)
    decide.add_argument("--reason", required=True)
    fork = actions.add_parser("fork")
    fork.add_argument("id")
    fork.add_argument("text")
    fork.add_argument("--reason", required=True)
    merge = actions.add_parser("merge")
    merge.add_argument("parents", nargs="+")
    merge.add_argument("--text", required=True)
    merge.add_argument("--reason", required=True)

    evidence = commands.add_parser("evidence", help="根拠と確認範囲の記録")
    actions = evidence.add_subparsers(dest="action", required=True)
    add = actions.add_parser("add")
    add.add_argument("--claim", required=True)
    add.add_argument("--source", required=True, help="文献名・識別子・取得元など")
    add.add_argument("--source-version", default="", help="版・発行日など。未記入は不明として保持")
    add.add_argument("--locator", default="", help="ページ・節・URL内の箇所")
    add.add_argument("--quote", default="", help="確認対象の原文（要約とは区別）")
    add.add_argument("--interpretation", required=True, help="主張との関係の解釈")
    add.add_argument("--relation", choices=["supports", "opposes", "qualifies", "unassessed"], required=True)
    add.add_argument("--level", choices=["search", "abstract", "passage"], required=True)
    add.add_argument("--status", choices=["unchecked", "checked", "inaccessible"], default="unchecked")
    add = actions.add_parser("from-document", help="保存本文に一致する引用だけを根拠として登録")
    add.add_argument("--claim", required=True)
    add.add_argument("--document", required=True)
    add.add_argument("--page", type=int, required=True)
    add.add_argument("--quote", required=True)
    add.add_argument("--interpretation", required=True)
    add.add_argument("--relation", choices=["supports", "opposes", "qualifies", "unassessed"], required=True)
    show = actions.add_parser("show")
    show.add_argument("id")
    flag = actions.add_parser("flag", help="根拠の問題を記録し、主張を再確認へ戻す")
    flag.add_argument("id")
    flag.add_argument("--reason", required=True)
    assess = actions.add_parser("assess", help="根拠を再確認。主張は自動的に確認済みにならない")
    assess.add_argument("id")
    assess.add_argument("--status", choices=["unchecked", "checked", "inaccessible"], required=True)
    assess.add_argument("--reason", required=True)
    review = actions.add_parser("review", help="Codexに登録原文と主張の関係を確認依頼")
    review.add_argument("id")
    review.add_argument("--timeout", type=float, default=180, help="Codex推論の制限秒数（既定: 180）")
    review.add_argument("--model", help="Codexのモデル名。省略時はCLI組み込みの既定モデル")
    return root


def dispatch(store, args):
    if args.command == "init":
        return store.init(args.title)
    if args.command == "start":
        store.init(args.title)
        if args.audience or args.venue:
            store.configure_study("初期研究設定", audience=args.audience, venue=args.venue)
        configure_options(store, args)
        return ResearchWorkflow(store).run(args.steps, max_seconds=args.seconds)
    if args.command == "status":
        return store.status()
    if args.command == "history":
        return store.history(args.claim)
    if args.command == "runs":
        return store.list_runs()
    if args.command == "run":
        if args.action == "show":
            return store.show_run(args.id)
        return store.abandon_run(args.id, args.reason)
    if args.command == "study":
        if args.action == "configure":
            settings = {key: getattr(args, key) for key in (
                "model", "review_model", "review_effort", "citation_seeds", "citation_limit", "citation_budget", "timeout", "reasoning_effort", "provider", "paper_limit", "pages_per_document", "query_limit", "search_budget", 'draft_checkpoint_readings', 'semantic_selection', 'selection_page_limit'
            ) if getattr(args, key, None) is not None}
            return store.configure_study(args.reason, args.question, args.audience, args.venue, settings)
        if args.action == "select":
            return store.select_claim(args.id, args.reason)
        if args.action == "guide":
            from .common import required
            text = required(args.text, "研究方針")
            store.event("study.guidance", text, {"actor": "human", "question": store.study_state()["question"]})
            store.db.commit()
            return store.study_state()
        if args.action == "export":
            return export_study(store, args.output, args.include_sources, pdf=args.pdf)
        configure_options(store, args)
        workflow = ResearchWorkflow(store)
        if args.action == "status":
            return workflow.summary()
        if args.action == "next":
            return {"state": store.study_state(), "choices": workflow.choices()}
        if args.action == "pause":
            return workflow.pause(args.reason)
        if args.action == "finalize":
            return workflow.finalize(args.reason)
        if args.action == "import-draft":
            return workflow.import_draft(args.file, args.reason)
        if args.action in ("run", "resume"):
            return workflow.run(args.steps, getattr(args, "reason", None), max_seconds=args.seconds)
        kind = {"orient": "orientation", "explore": "exploration", "compare": "comparison",
                "read": "reading", "revise-draft": "revise_draft"}.get(args.action, args.action)
        with study_lock(store):
            workflow.recover()
            parameters = {"document": args.id, "page": args.page} if args.action == "read" else {}
            return workflow.perform(kind, force=args.force, **parameters)
    if args.command == "task":
        if args.action == "list":
            return store.list_tasks()
        if args.action == "show":
            return store.show_task(args.id)
        return ResearchWorkflow(store).cancel_task(args.id, args.reason)
    if args.command == "artifact":
        return store.list_artifacts() if args.action == "list" else store.show_artifact(args.id)
    if args.command == "issue":
        return store.list_issues() if args.action == "list" else store.respond_issue(args.id, args.status, args.reason, args.uncertainty)
    if args.command == "paper":
        if args.action == "graph":
            return store.citation_graph()
        if args.action == "related":
            from .discovery import related_papers
            with study_lock(store):
                return related_papers(store, args.id, args.direction, args.limit)
        if args.action == "search":
            return search_papers(store, args.query, args.provider, args.limit)
        if args.action == "list":
            return store.list_papers()
        if args.action == "show":
            return store.show_paper(args.id)
        if args.action == "add":
            if args.openalex:
                import re
                from .discovery import request, parse_work, BASE
                if not re.fullmatch(r"W[0-9]+", args.openalex) or any((args.arxiv, args.url, args.pdf_url, args.title, args.source_version)):
                    raise ResearchError("--openalex はW数字の単独指定で使ってください。")
                return store.add_paper(parse_work(request(BASE + "/" + args.openalex)))
            return register_paper(store, args.title, args.url, args.pdf_url, args.arxiv, args.source_version)
        return fetch_paper(store, args.id, args.url, args.file, args.abstract)
    if args.command == "search":
        return store.list_searches() if args.action == "list" else store.show_search(args.id)
    if args.command == "document":
        if args.action == "list":
            return store.list_documents()
        if args.action == "show":
            return store.show_document(args.id, args.page)
        if args.action == "find":
            return store.find_passages(args.query, args.document, args.limit)
        if args.action == "review":
            validate_review_options(args.timeout, args.model)
            text = store.document_page(args.id, args.page)
            if not text.strip() or len(text) > 30000:
                raise ResearchError("指定ページが空、または30,000文字を超えます。from-documentで該当箇所を指定してください。")
            evidence = store.add_document_evidence(args.claim, args.id, args.page, text,
                                                   "選択ページの自動読解用入力。主張との関係は未評価。", "unassessed")
            review = review_evidence(store, evidence["id"], timeout=args.timeout, model=args.model)
            return {"evidence": store.show_evidence(evidence["id"]), "review": review}
        document = store.document_row(args.id, include_content=True)
        path = Path(args.output).expanduser()
        with path.open("xb") as handle:
            handle.write(document["content"])
        return {"document_id": args.id, "output": str(path.resolve()), "sha256": document["sha256"], "origin": document["origin"]}
    if args.command == "claim":
        if args.action == "add":
            return store.add_claim(args.text)
        if args.action == "show":
            return store.show_claim(args.id)
        if args.action == "revise":
            return store.revise_claim(args.id, args.text, args.reason)
        if args.action == "fork":
            return store.fork_claim([args.id], args.text, args.reason)
        if args.action == "merge":
            if len(args.parents) < 2:
                raise ResearchError("統合には複数の主張が必要です。")
            return store.fork_claim(args.parents, args.text, args.reason, kind="merge")
        return store.decide_claim(args.id, args.status, args.reason)
    if args.action == "from-document":
        return store.add_document_evidence(args.claim, args.document, args.page, args.quote, args.interpretation, args.relation)
    if args.action == "add":
        return store.add_evidence(args.claim, args.source, args.source_version, args.locator,
                                  args.quote, args.interpretation, args.relation, args.level, args.status)
    if args.action == "show":
        return store.show_evidence(args.id)
    if args.action == "flag":
        return store.assess_evidence(args.id, "disputed", args.reason)
    if args.action == "review":
        return review_evidence(store, args.id, timeout=args.timeout, model=args.model)
    return store.assess_evidence(args.id, args.status, args.reason)


def main(argv=None):
    args = parser().parse_args(argv)
    store = None
    try:
        if args.command == "codex":
            result = codex_status()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["chatgpt_authenticated"] else 1
        if args.command == "eval":
            from . import evaluation
            if args.action == "extraction":
                from .extraction_eval import run
                result = run(args.output, args.model, args.cases, args.timeout)
            elif args.action == 'verification-regression':
                from .verification_eval import run
                result = run(args.output, args.model, args.cases, args.timeout, args.result)
            elif args.action == 'selection':
                from .retrieval_eval import run
                result = run(args.output, args.cases, args.model, args.ranking, args.k, args.timeout)
            elif args.action == "run":
                if not 1 <= args.seconds <= 7200:
                    raise ResearchError("評価の時間上限は1〜7200秒です。")
                result = evaluation.run_evaluation(args.output, args.cases, args.model, args.review_model, args.seconds, args.resume, args.methods)
            elif args.action == "packet":
                result = evaluation.packet(args.output, args.review_model)
            elif args.action == "import-scores":
                result = evaluation.import_scores(args.output, args.file)
            else:
                result = evaluation.report(args.output)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "start" and (not args.model or not args.review_model or args.model == args.review_model):
            raise ResearchError("新規研究には異なる --model と --review-model を明示してください。")
        store = ResearchStore(args.db, initialize=args.command in ("init", "start"))
        result = dispatch(store, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ResearchError, sqlite3.Error, OSError, KeyError, TypeError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    finally:
        if store is not None:
            store.close()
