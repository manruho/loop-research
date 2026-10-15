"""Fact-level model judgments plus deterministic source/span validation."""
from .schema import obj, array, string
from .common import ResearchError
from . import ledger

TEXT = string(minimum=1)
INTEGER = {"type": "integer", "minimum": 1, "maximum": 1000}
VERIFY = obj(
    facts=array(obj(id=string(minimum=1, maximum=30), block_id=string(minimum=1, maximum=30),
                    field=string(("text", "qualification")), text=string(minimum=1, maximum=1800), occurrence=INTEGER,
                    sources=array(obj(evidence_id=string(minimum=1, maximum=30), quote=string(minimum=1, maximum=1800), occurrence=INTEGER), maximum=8),
                    verdict=string(("supported", "partial", "contradicted", "undetermined")),
                    reason=TEXT, qualification=string(maximum=2000)), maximum=150),
    coverage=array(obj(block_id=string(minimum=1, maximum=30),
                       status=string(("complete", "no_facts", "incomplete")), reason=TEXT), minimum=1, maximum=110),
    ledger_claims=array(ledger.CLAIM, maximum=150),
    ledger_coverage=array(obj(block_id=string(minimum=1, maximum=30),
                              status=string(("complete", "no_facts", "incomplete")), reason=TEXT), minimum=1, maximum=110),
    limitations=array(TEXT, minimum=1, maximum=12),
)
PROMPT = (
    "Inspect MT001 (title), every MH target (heading), and every MU target (uncertainties); verification_targets enumerates all prose. "
    "source_path resolves inside draft.content; string targets use field=text and qualification is empty. "
    "Preserve the complete proposition, negation, temporal scope and qualifications. A quoted older count "
    "explicitly superseded is not an assertion of the current count. Never extract only a number and omit "
    "the sentence's denial. Each comparison must correspond to what the sentence actually asserts. "
    "Assess population, experimental vs observational design, effect direction, modality, measurements "
    "and operational conditions separately. Missing limiting conditions require partial/undetermined "
    "and explanation even when the source quote matches verbatim. "
    "Extract EVERY empirically checkable assertion presented as fact, including factual claims about sources or studies, from ALL manuscript blocks, including those "
    "labeled position or hypothesis when the label hides an asserted empirical claim. Exclude clearly "
    "identified conjectures, normative/value premises, stipulated definitions and analytic/logical "
    "arguments that are not empirical assertions; the reviewer evaluates their reasoning separately. "
    "Extract factual self-reports about search/reading/evidence status into ledger_claims, not literature facts. "
    "Every ledger_fact block requires extracted ledger assertions with exact text spans and record paths; "
    "a ledger_fact label is not evidence. Extract scientific assertions misclassified there as literature facts too. "
    "CLASSIFICATION BOUNDARY: uncertainty statements such as 'novelty remains unresolved', 'effectiveness "
    "is not established', 'this proposal has not demonstrated benefits' or 'these passages cannot establish "
    "an independent effect' describe epistemic limits, not recorded actions. Do not invent ledger fields "
    "for them. A concrete statement that the author performed or did not perform a search, experiment "
    "or tool call IS an activity assertion and must remain undetermined when its record is unavailable. "
    "Claims about what a cited study tested belong to literature facts with source spans. Normative "
    "proposals and conditional future evaluations are not factual activity assertions. "
    "COVERAGE CHECKLIST: inspect every block's text AND qualification, particularly metadata-only paper "
    "IDs, evidence_status (including unchecked/disputed), evidence_relation (including unassessed), and "
    "each individual paper/page and document-to-paper mapping. Split long conjunctions into separate "
    "G assertions; do not pack an entire ledger paragraph into one claim with eight truncated checks. "
    "Use every relevant P/E/D ID named by the manuscript; a partial mapping cannot be complete coverage. "
    "For each use G001 etc, an exact manuscript substring, and checks against ledger_snapshot.records. "
    "Use existing paths only, e.g. /search_count equals expected_json 6, /search_providers contains "
    "expected_json \"arxiv\", /paper_read_pages/P001 contains expected_json 58. Prefix /records/ is also accepted. "
    "Use /paper_titles/Pxxx to bind named papers to their IDs, /discovery_deferred for the recorded draft flag, "
    "and /citation_graph only for recorded links, never to prove no external citation tracing occurred. "
    "unchecked is a stored evidence status, not proof that no human ever reviewed a source. "
    "Such unrecorded activity assertions remain undetermined even when an evidence_status check matches. "
    "expected_json is a JSON-encoded value: wrap string values in double quotes and escape those quotes "
    "inside the output JSON. Missing fields cannot establish an absence or a negative claim. "
    "A check has a JSON Pointer path, operator equals/not_equals/contains/all_equal and expected_json encoding the "
    "asserted value (not the observed value). not_equals compares known, same-type scalars for an explicitly negative comparison; it never "
    "establishes absence of unrecorded activity or a missing field. Keep negation within the assertion span. "
    "Split compound record assertions. "
    "Use all_equal only for an explicitly universal statement; contains only for membership. Missing "
    "record paths remain undetermined. Do not use ledger claims to support scientific findings. "
    "Report ledger_coverage for EVERY block, including qualification: complete/no_facts/incomplete. "
    "Do not omit record assertions merely because they are self-reports or labeled interpretation. "
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
    from .roles import verification_blocks
    result = copy.deepcopy(result)
    manuscript = {b["id"]: b for b in verification_blocks(data["draft"]["content"])}
    coverage = result["coverage"]
    if len({c["block_id"] for c in coverage}) != len(coverage) or {c["block_id"] for c in coverage} != set(manuscript):
        raise ResearchError("すべての段落の事実抽出範囲を記録してください。")
    result["ledger_claims"] = ledger.normalize(data, result["ledger_claims"], manuscript)
    ledger_coverage = result["ledger_coverage"]
    if len({c["block_id"] for c in ledger_coverage}) != len(ledger_coverage) or {c["block_id"] for c in ledger_coverage} != set(manuscript):
        raise ResearchError("すべての段落の実行記録抽出範囲を記録してください。")
    for item in ledger_coverage:
        found = any(f["block_id"] == item["block_id"] for f in result["ledger_claims"])
        if item["status"] == "no_facts" and found:
            raise ResearchError("実行記録の範囲判定と抽出結果が矛盾しています。")
        if manuscript[item["block_id"]]["kind"] == "ledger_fact" and not found:
            item["model_status"] = item["status"]
            item["status"] = "incomplete"
            item["reason"] = "Host coverage gate: ledger_fact block has no extracted ledger assertions. " + item["reason"]
    result["ledger_snapshot_sha256"] = data["ledger_snapshot"]["sha256"]
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
        fact['context_id'] = fact['block_id'] + '.' + fact['field']
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
        if manuscript[item["block_id"]]["kind"] == "fact" and not found and not any(f["block_id"] == item["block_id"] for f in result["ledger_claims"]) and item["status"] != "incomplete":
            item["model_status"] = item["status"]
            item["status"] = "incomplete"
            item["host_adjustments"] = ["fact-labeled block has no extracted assertions"]
            item["reason"] = "Host coverage gate: fact-labeled block has no extracted assertions. " + item["reason"]
    result['assertion_contexts'] = {f['context_id']: manuscript[f['block_id']][f['field']]
                                    for f in result['facts'] + result['ledger_claims']}
    return result


def blocking(result):
    return ([f for f in result["facts"] if f["verdict"] != "supported"]
            + [dict(f, ledger=True) for f in result.get("ledger_claims", []) if f["verdict"] != "supported"]
            + [c for c in result["coverage"] if c["status"] == "incomplete"]
            + [dict(c, ledger=True) for c in result.get("ledger_coverage", []) if c["status"] == "incomplete"])
