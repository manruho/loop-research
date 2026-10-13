"""Human-readable manuscript and complete research records from one DB snapshot."""

import hashlib
import html
import json
import os
import tempfile
from pathlib import Path

from .common import ResearchError, label, now
from .roles import blocks
from .workflow import ResearchWorkflow


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def bib_escape(value):
    replacements = {"\\": "\\textbackslash{}", "{": "\\{", "}": "\\}", "\n": " "}
    return "".join(replacements.get(char, char) for char in str(value))


def bibliography(store):
    entries = []
    for paper in store.list_papers():
        row = store.show_paper(paper["id"])
        fields = {"title": row["title"], "url": row["url"]}
        if row["authors"]:
            fields["author"] = " and ".join(row["authors"])
        if row["published"]:
            fields["year"] = row["published"][:4]
        if row["doi"]:
            fields["doi"] = row["doi"]
        if row["source_version"]:
            fields["note"] = "Source version: " + row["source_version"]
        entries.append("@misc{" + row["id"] + ",\n" + ",\n".join(
            "  " + key + " = {" + bib_escape(value) + "}" for key, value in fields.items()
        ) + "\n}")
    return "\n\n".join(entries) + "\n"


def render_draft(store, artifact, status, current=True):
    if artifact is None:
        return "# Research outcome\n\nNo manuscript has been produced.\n\nResearch status: " + status + "\n"
    content = artifact["content"]
    lines = [
        "# " + content["title"], "",
        "> Research status: " + status + ". " + (
            "This is the current research draft; internal AI review is not academic approval."
            if current else "Historical/outdated draft. Do not treat it as the current research conclusion."
        ), "",
    ]
    references = {}
    def block_text(block):
        marks = []
        for eid in block["evidence_ids"]:
            evidence = store.show_evidence(eid)
            source = evidence["document_source"]
            key = source["paper_id"] if source else eid
            if key not in references:
                if source:
                    paper = store.show_paper(key)
                    authors = ", ".join(paper["authors"]) or "Author metadata unavailable"
                    date = paper["published"][:4] if paper["published"] else "n.d."
                    text = f"{authors} ({date}). {paper['title']}. {paper['url']}"
                    if paper["source_version"]:
                        text += ". Version: " + paper["source_version"]
                else:
                    text = evidence["source"] + " (manually supplied source attribution)"
                references[key] = (len(references) + 1, html.escape(text))
            mark = "[^" + str(references[key][0]) + "]"
            if mark not in marks:
                marks.append(mark)
        text = block["text"] + (" " + "".join(marks) if marks else "")
        if block["qualification"].strip():
            text += "\n\n*" + block["qualification"] + "*"
        text += "\n\n<!-- block:" + block["id"] + " claims:" + ",".join(block["claim_ids"]) \
                + " evidence:" + ",".join(block["evidence_ids"]) + " kind:" + block["kind"] + " -->"
        return text
    lines.extend(["## Abstract", ""])
    for block in content["abstract_blocks"]:
        lines.extend([block_text(block), ""])
    for section in content["sections"]:
        lines.extend(["## " + section["heading"], ""])
        for block in section["blocks"]:
            lines.extend([block_text(block), ""])
    lines.extend(["## Outstanding uncertainties", ""])
    lines.extend("- " + value for value in content["uncertainties"])
    lines.extend(["", "## References", ""])
    for _, (number, text) in references.items():
        lines.append(f"[^{number}]: {text}")
    return "\n".join(lines) + "\n"


def uncertainties(store, workflow):
    state = store.study_state()
    lines = ["# 研究上の不確実性", "", "状態: " + state["status"], "", state["reason"], "",
             "## 読解・探索の限界", "", "全文精読・新規性の証明・採択の保証は行っていません。", ""]
    for search in store.list_searches():
        lines.append(f"- {search['id']}: {search['provider']} / {search['query']} / {search['status']}。{search['error'] or '件数上限のある探索。'}")
    for paper in store.list_papers():
        item = store.show_paper(paper["id"])
        if not item["source_version"]:
            lines.append(f"- {item['id']}: 版の情報は不明。原資料はハッシュで識別。")
        if not item["documents"]:
            lines.append(f"- {item['id']}: 本文は未取得。")
        for attempt in item["fetches"]:
            if attempt["status"] != "completed":
                lines.append(f"- {attempt['id']}: {attempt['error'] or '未完了の取得'}")
        for doc in item["documents"]:
            seen = [r["page"] for r in workflow.scope()["readings"] if r["document_id"] == doc["id"]]
            lines.append(f"- {doc['id']}: {doc['origin']} / {doc['status']}。保存単位数 {len(doc['pages'])}、AI読解の記録がある単位 {sorted(set(seen))}。")
    lines.extend(["", "## 未確認の根拠", ""])
    for row in store.db.execute("SELECT id FROM evidence WHERE status!='checked' ORDER BY id"):
        evidence = store.show_evidence(label(row[0], "E"))
        lines.append(f"- {evidence['id']}: {evidence['status']} / {evidence['level']} / {evidence['locator']}")
    lines.extend(["", "## 査読の指摘と残る不確実性", ""])
    for issue in store.list_issues():
        lines.append(f"- {issue['id']} ({issue['draft_id']}): {issue['severity']} / {issue['status']}。{issue['content']['explanation']} 応答: {issue['response'] or '未応答'} 残る不確実性: {issue['remaining_uncertainty']}")
    draft = store.latest_artifact("draft")
    if draft:
        lines.extend(["", "## 草稿が明示する限界", ""] + ["- " + note for note in draft["content"]["uncertainties"]])
    return "\n".join(lines) + "\n"


def export_study(store, output, include_sources=False):
    target = Path(output).expanduser().resolve()
    if target.exists():
        raise ResearchError("出力先はすでに存在します。新しいディレクトリを指定してください。")
    workflow = ResearchWorkflow(store)
    try:
        store.db.execute("BEGIN")
        state = store.study_state()
        snapshot = store.domain_fingerprint()
        summary = workflow.summary()
        current = None
        if summary["current_draft"]:
            current = store.show_artifact(summary["current_draft"])
        draft = current or store.latest_artifact("draft")
        claims = [store.show_claim(label(row[0], "C")) for row in store.db.execute("SELECT id FROM claims ORDER BY id")]
        evidence = [store.show_evidence(label(row[0], "E")) for row in store.db.execute("SELECT id FROM evidence ORDER BY id")]
        artifacts = [store.show_artifact(item["id"]) for item in store.list_artifacts()]
        literature = {
            "papers": [store.show_paper(item["id"]) for item in store.list_papers()],
            "documents": store.list_documents(),
            "searches": [store.show_search(item["id"]) for item in store.list_searches()],
            "reading_scope": workflow.scope(), "citation_graph": store.citation_graph(),
        }
        graph = {"question": state["question"], "claims": claims, "edges": store.claim_graph(),
                 "domains": [a["content"]["domains"] for a in artifacts if a["kind"] == "orientation"]}
        decisions = store.history()
        files = {
            "manifest.json": None,
            "study.json": json_text(summary), "research-map.json": json_text(graph),
            "claims.json": json_text(claims), "evidence.json": json_text(evidence),
            "ledger-verification.json": json_text([
                {"verification_id": a["id"], "snapshot": a["input"].get("ledger_snapshot"),
                 "claims": a["content"].get("ledger_claims", []),
                 "coverage": a["content"].get("ledger_coverage", [])}
                for a in artifacts if a["kind"] == "verify"]),
            "fact-verification.json": json_text([{"artifact": store.show_artifact(v["id"]), "facts": store.verification_rows(v["id"])} for v in store.list_artifacts("verify")]),
            "literature.json": json_text(literature), "artifacts.json": json_text(artifacts),
            "tasks.json": json_text([store.show_task(item["id"]) for item in store.list_tasks()]),
            "decisions.json": json_text(decisions), "reviews.json": json_text(store.list_issues()),
            "draft.md": render_draft(store, draft, state["status"], current is not None),
            "uncertainties.md": uncertainties(store, workflow), "references.bib": bibliography(store),
        }
        if draft:
            editable = json.loads(json.dumps(draft["content"]))
            for block in blocks(editable):
                block.pop("id", None)
            files["draft-editable.json"] = json_text(editable)
        lines = ["# 研究地図", "", state["question"], "", "## 主張と立場", ""]
        for claim in claims:
            lines.append(f"- {claim['id']} v{claim['revision']} [{claim['status']}]: {claim['text']}")
        lines.extend(["", "## 分岐・統合・改訂", ""])
        for edge in graph["edges"]:
            lines.append(f"- {edge['parent_id']} v{edge['parent_revision']} → {edge['child_id']} v{edge['child_revision']} ({edge['kind']}): {edge['reason']}")
        lines.extend(["", "## 現在の判断", "", state["reason"], "",
                      "根拠はevidence.json、競合案の比較・論証・査読はartifacts.json、変更理由はdecisions.jsonに記録。"])
        files["research-map.md"] = "\n".join(lines) + "\n"
        source_files = {}
        if include_sources:
            for item in store.list_documents():
                row = store.document_row(item["id"], include_content=True)
                suffix = {"pdf": ".pdf", "abstract": ".json", "text": ".txt"}[row["origin"]]
                source_files["sources/" + item["id"] + suffix] = row["content"]
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".research-export-", dir=target.parent) as directory:
        stage = Path(directory)
        hashes = {}
        for name, value in files.items():
            if value is None:
                continue
            payload = value.encode("utf-8")
            (stage / name).write_bytes(payload)
            hashes[name] = hashlib.sha256(payload).hexdigest()
        for name, payload in source_files.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            hashes[name] = hashlib.sha256(payload).hexdigest()
        manifest = {
            "format_version": 1, "exported_at": now(), "research_fingerprint": snapshot,
            "status": state["status"], "current_draft": current["id"] if current else None,
            "historical_draft": draft["id"] if draft and not current else None,
            "sources_included": include_sources, "files": hashes,
            "notice": "AIの内部査読と人間の研究判断を区別。検索・引用照合は新規性・学術的正しさの保証ではない。",
        }
        (stage / "manifest.json").write_text(json_text(manifest), encoding="utf-8")
        if target.exists():
            raise ResearchError("出力先が作成されたため上書きせず停止しました。")
        os.rename(stage, target)
    return {"output": str(target), "manifest": str(target / "manifest.json"),
            "status": state["status"], "current_draft": manifest["current_draft"]}
