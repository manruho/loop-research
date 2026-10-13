"""Fact-level model judgments plus deterministic source/span validation."""
from .schema import obj, array, string
from .common import ResearchError

TEXT = string(minimum=1)
INTEGER = {"type": "integer", "minimum": 1, "maximum": 1000}
VERIFY = obj(
    facts=array(obj(id=string(minimum=1, maximum=30), block_id=string(minimum=1, maximum=30),
                    field=string(("text", "qualification")), text=string(minimum=1, maximum=1800), occurrence=INTEGER,
                    sources=array(obj(evidence_id=string(minimum=1, maximum=30), quote=string(minimum=1, maximum=1800), occurrence=INTEGER), maximum=8),
                    verdict=string(("supported", "partial", "contradicted", "undetermined")),
                    reason=TEXT, qualification=string(maximum=2000)), maximum=150),
    coverage=array(obj(block_id=string(minimum=1, maximum=30),
                       status=string(("complete", "no_facts", "incomplete")), reason=TEXT), minimum=1, maximum=88),
    limitations=array(TEXT, minimum=1, maximum=12),
)
PROMPT = (
    "Extract EVERY empirically checkable assertion presented as fact, including factual claims about sources or studies, from ALL manuscript blocks, including those "
    "labeled position or hypothesis when the label hides an asserted empirical claim. Exclude clearly "
    "identified conjectures, normative/value premises, stipulated definitions and analytic/logical "
    "arguments that are not empirical assertions; the reviewer evaluates their reasoning separately. "
    "Exclude self-reports about this manuscript's search/reading/verification process; the reviewer "
    "compares those with the supplied host logs rather than requiring a scientific-paper citation. "
    "Do not demand experiments to establish a normative proposal. Split compound factual assertions; preserve negation, population, numbers and "
    "conditions. fact.text must be an exact contiguous substring of its block; do not paraphrase. "
    "Use F001, F002 etc. field MUST identify text or qualification. Extract factual assertions from "
    "both fields; do not locate qualification text in the main text. occurrence is the 1-based "
    "occurrence of that exact span within the selected field of its block. "
    "Evaluate substantive support, not just quotation existence: supported, partial, contradicted or "
    "undetermined. For each source use a supplied E ID and an exact contiguous substring of its quote, "
    "with 1-based occurrence within that quote. Each source_spans entry is a host-validated linkage "
    "from the E ID to an immutable document_id, page, sha256 and exact stored text. A live URL is NOT "
    "required; do not reject linked excerpts because URLs are absent. Use these supplied quotations "
    "to assess content support, separately from human verification and source authenticity. "
    "Inspect the citations on the specific block; sources not cited there are a citation gap even if relevant. Do not invent uninspected context. A reported association does not support causality, and normative "
    "premises are not empirical conclusions. Unchecked/abstract evidence remains provisional even if "
    "the excerpt supports the assertion. An assertion without an inspectable source is undetermined. "
    "Report coverage for EVERY block. complete means all factual assertions were extracted, no_facts "
    "means none were found, incomplete means unresolved extraction. Disclose extraction uncertainty. "
    "This is an AI assessment, never a human verification or proof of correctness."
)


def locate(text, quote, occurrence=1):
    start = -1
    for _ in range(occurrence):
        start = text.find(quote, start + 1)
        if start < 0:
            raise ResearchError("検証対象の引用または出現位置が保存本文に一致しません。")
    return start, start + len(quote)


def normalize(data, result):
    import copy
    from .roles import blocks
    result = copy.deepcopy(result)
    manuscript = {b["id"]: b for b in blocks(data["draft"]["content"])}
    coverage = result["coverage"]
    if len({c["block_id"] for c in coverage}) != len(coverage) or {c["block_id"] for c in coverage} != set(manuscript):
        raise ResearchError("すべての段落の事実抽出範囲を記録してください。")
    ids = set()
    for fact in result["facts"]:
        if fact["id"] in ids or fact["block_id"] not in manuscript:
            raise ResearchError("事実主張のIDが重複しているか、対象段落がありません。")
        # Use the common identifier validator, no model-created cross-study IDs.
        from .common import identifier
        from .common import label
        fact["id"] = label(identifier(fact["id"], "F"), "F")
        if fact["id"] in ids:
            raise ResearchError("事実主張のIDが重複しています。")
        ids.add(fact["id"])
        fact["model_verdict"] = fact["verdict"]
        fact["host_adjustments"] = []
        try:
            fact["start"], fact["end"] = locate(manuscript[fact["block_id"]][fact["field"]], fact["text"], fact["occurrence"])
        except ResearchError as exc:
            raise ResearchError(f"{fact['id']} {fact['block_id']}.{fact['field']}: {exc}") from exc
        seen = set()
        for source in fact["sources"]:
            eid = source["evidence_id"]
            base = data["source_spans"].get(eid)
            if not base:
                raise ResearchError("検証の根拠が保存原資料に結び付いていません。")
            try:
                start, end = locate(base["quote"], source["quote"], source["occurrence"])
            except ResearchError as exc:
                raise ResearchError(f"{fact['id']} source {eid}: {exc}") from exc
            marker = (eid, start, end)
            if marker in seen:
                raise ResearchError("同じ根拠範囲が重複しています。")
            seen.add(marker)
            source.update(document_id=base["document_id"], page=base["page"],
                          start=base["start"] + start, end=base["start"] + end,
                          sha256=base["sha256"], source_version=base["source_version"])
        uncited = [s["evidence_id"] for s in fact["sources"] if s["evidence_id"] not in manuscript[fact["block_id"]]["evidence_ids"]]
        if uncited and fact["verdict"] == "supported":
            fact["verdict"] = "partial"
            fact["host_adjustments"].append("supporting source not cited by block")
            fact["reason"] = "Supporting source is not cited by this block: " + ", ".join(uncited) + ". " + fact["reason"]
            fact["qualification"] = "A supporting source must be explicitly cited in the manuscript. " + fact["qualification"]
        if fact["verdict"] != "undetermined" and not fact["sources"]:
            raise ResearchError("支持・反証の判定には原資料の範囲が必要です。")
        if fact["verdict"] != "supported" and not fact["qualification"].strip():
            raise ResearchError("未支持の事実主張には留保と不足内容が必要です。")
    for item in coverage:
        found = any(f["block_id"] == item["block_id"] for f in result["facts"])
        if item["status"] == "no_facts" and found:
            raise ResearchError("事実なしという範囲判定と抽出結果が矛盾しています。")
        if manuscript[item["block_id"]]["kind"] == "fact" and not found and item["status"] != "incomplete":
            item["model_status"] = item["status"]
            item["status"] = "incomplete"
            item["host_adjustments"] = ["fact-labeled block has no extracted assertions"]
            item["reason"] = "Host coverage gate: fact-labeled block has no extracted assertions. " + item["reason"]
    return result


def blocking(result):
    return [f for f in result["facts"] if f["verdict"] != "supported"] + [c for c in result["coverage"] if c["status"] == "incomplete"]
