"""Research role contracts: grounded exploration, English drafting, and criticism."""

import re

from .common import ResearchError
from .schema import array, obj, string


TEXT = string(minimum=1)
NOTES = array(TEXT, minimum=1, maximum=12)
IDS = array(string(minimum=1, maximum=30), maximum=30)
KINDS = ("observation", "author_interpretation", "proposal", "hypothesis")
CLAIM_KINDS = ("descriptive", "interpretive", "normative", "proposal", "hypothesis")
QUERY = obj(query=string(minimum=1, maximum=160), provider=string(("arxiv", "crossref", "openalex")), purpose=TEXT)
ORIENTATION = obj(
    interpretation=TEXT, domains=array(TEXT, minimum=1, maximum=8),
    search_queries=array(QUERY, minimum=1, maximum=10),
    key_terms=array(string(minimum=1, maximum=80), minimum=1, maximum=12),
    recommendation=string(("proceed", "consult", "hold")), reason=TEXT, uncertainties=NOTES,
)
READING = obj(
    relevance=string(("relevant", "marginal", "unrelated")),
    findings=array(obj(kind=string(KINDS), statement=TEXT,
                       excerpt=string(minimum=1, maximum=1800), limitations=NOTES), maximum=8),
    strongest_counterargument=TEXT, open_questions=NOTES,
)
CANDIDATE = obj(
    text=TEXT, kind=string(CLAIM_KINDS), scope=TEXT, audience=TEXT,
    assumptions=NOTES, supporting_evidence_ids=IDS, opposing_evidence_ids=IDS,
    counterarguments=NOTES, novelty=TEXT,
    overlaps=array(obj(paper_id=string(minimum=1, maximum=30), overlap=TEXT, difference=TEXT,
                       verification_scope=string(("metadata", "abstract", "passages"))), maximum=12),
    open_questions=NOTES,
)
EXPLORATION = obj(candidates=array(CANDIDATE, maximum=4), reason=TEXT, limitations=NOTES)
COMPARISON = obj(
    decision=string(("proceed", "hold", "withdraw")), selected_claim_id=string(maximum=30),
    reason=TEXT, assessments=array(obj(
        claim_id=string(minimum=1, maximum=30), novelty=TEXT, evidence_strength=TEXT,
        strongest_objection=TEXT, response=TEXT, unresolved=NOTES,
    ), minimum=1, maximum=30),
    requested_searches=array(QUERY, maximum=5), limitations=NOTES,
)
PREMISE = obj(statement=TEXT, kind=string(CLAIM_KINDS), evidence_ids=IDS, assumptions=NOTES)
OUTLINE = obj(
    title=TEXT, central_claim_id=string(minimum=1, maximum=30), contribution=TEXT,
    sections=array(obj(
        heading=TEXT, purpose=TEXT, premises=array(PREMISE, minimum=1, maximum=8),
        counterarguments=array(obj(objection=TEXT, response=TEXT, evidence_ids=IDS), maximum=5),
        limitations=NOTES,
    ), minimum=3, maximum=10),
    open_questions=NOTES,
)
BLOCK = obj(
    text=string(minimum=1, maximum=8000),
    kind=string(("fact", "interpretation", "position", "hypothesis")),
    evidence_ids=IDS, claim_ids=IDS, qualification=string(maximum=2000),
)
DRAFT = obj(
    title=TEXT, abstract_blocks=array(BLOCK, minimum=1, maximum=4),
    sections=array(obj(role=string(("introduction", "related_work", "position", "counterarguments",
                                    "implications", "limitations", "conclusion")),
                       heading=TEXT, blocks=array(BLOCK, minimum=1, maximum=12)), minimum=7, maximum=7),
    uncertainties=NOTES,
)
FINDING = obj(
    severity=string(("critical", "major", "minor")),
    category=string(("novelty", "citation", "logic", "counterexample", "scope", "contribution")),
    target_type=string(("draft", "block", "claim", "evidence", "paper")),
    target_id=string(minimum=1, maximum=30), explanation=TEXT, evidence_ids=IDS,
    suggested_action=string(("search", "read", "redesign", "revise", "withdraw", "hold")),
    remaining_uncertainty=TEXT,
)
REVIEW = obj(
    decision=string(("ready_for_human", "revise", "hold", "withdraw")),
    synthesis=TEXT, findings=array(FINDING, maximum=100),
    requested_searches=array(QUERY, maximum=5), unresolved=NOTES,
    prior_issue_assessments=array(obj(issue_id=string(minimum=1, maximum=30),
                                     status=string(("resolved", "persists", "not_applicable")),
                                     reason=TEXT, remaining_uncertainty=TEXT), maximum=50),
)
REDESIGN = obj(
    decision=string(("revise", "fork", "merge", "research", "hold", "withdraw")),
    text=string(), kind=string(CLAIM_KINDS), scope=TEXT, reason=TEXT,
    parent_claim_ids=IDS, evidence_ids=IDS, assumptions=NOTES, counterarguments=NOTES,
    novelty=TEXT, open_questions=NOTES, requested_searches=array(QUERY, maximum=5),
    issue_responses=array(obj(issue_id=string(minimum=1, maximum=30), response=TEXT,
                              remaining_uncertainty=TEXT), maximum=20),
)
DECISION = obj(choice_id=string(minimum=1, maximum=30), reason=TEXT, uncertainty=TEXT)

PROMPTS = {
    "orientation": (
        "Interpret the research question, identify adjacent fields and competing concepts, and "
        "propose concise English search queries covering synonyms, adjacent disciplines, and counterevidence. "
        "Use arxiv, crossref and openalex across distinct purposes. arxiv supports short phrases and explicit Boolean queries: prefer "
        "established phrases (e.g. AI Scientist, automation bias), not long invented sentences. "
        "Include negative/competing searches. Recommend consultation only for a material ambiguity."
    ),
    "reading": (
        "Read only the supplied page/excerpt. Classify what it actually says as observation, "
        "author_interpretation, proposal, or hypothesis. Each excerpt must be an exact substring "
        "of page_text and at most 1800 characters. Report the strongest objection and gaps. "
        "Do not repair typography, remove line-break hyphens, reorder columns, or join non-adjacent "
        "spans in quotations. Use short contiguous excerpts; if extraction seems broken, disclose "
        "the problem and return fewer or no findings instead of reconstructing a quotation. "
        "Do not claim to have read other pages or the whole paper. Unrelated material may have no findings."
    ),
    "exploration": (
        "Explore 2-4 meaningfully competing positions, with scope, audience, assumptions, the strongest "
        "counterarguments, and bounded novelty relative to the supplied literature. Evidence references "
        "must be provided E IDs; their texts are in excerpts via excerpt_id. Compare against actual "
        "existing positions fairly. Cite only provided P IDs for overlaps and disclose whether metadata, "
        "abstract, or passages were seen. A normative proposal needs an explicit value premise. "
        "Return zero candidates if no defensible position is found, explaining why."
    ),
    "comparison": (
        "Compare every supplied candidate without numeric novelty scores. Select one tentative position "
        "only if it has a defensible contribution, explicit limits and some grounded evidence. No search proves novelty. "
        "A position paper may make a normative or conceptual contribution without a new experiment. "
        "Lack of measured efficacy is not by itself a reason to reject a clearly justified value premise "
        "and limited proposal; disclose efficacy as a hypothesis to test. Unconfirmed novelty must be "
        "reported as uncertain rather than presumed new or presumed worthless. "
        "Compare the strongest objections and unresolved premises. Hold or withdraw if "
        "the literature already makes the proposed contribution or evidence is inadequate. "
        "selected_claim_id must be empty when not proceeding. Additional searches must target a specific gap."
    ),
    "outline": (
        "Design an argument for the selected position paper: background, fair related work, the central "
        "position, strongest counterarguments, implications, limitations and conclusion. Identify premises "
        "and their types, evidence IDs, and normative assumptions. Do not let an observation alone imply "
        "a mandatory policy. Every factual premise MUST include at least one supplied evidence ID; "
        "if no evidence supports it, omit it or honestly classify it as an assumption or hypothesis. "
        "Do not classify unverified proposed benefits as facts. Use only the selected claim and supplied "
        "evidence. English headings are preferred."
    ),
    "draft": (
        "Write a complete editable ENGLISH position-paper draft from the outline and evidence ledger. "
        "Aim for a concise 1200-2000 word working draft; do not pad it to meet a count. "
        "Use abstract_blocks and sections for introduction, related work, position/argument, counterarguments, "
        "implications, limitations and conclusion. Each paragraph is a block. Every fact requires evidence_ids. "
        "Map each major block to the selected C ID via claim_ids. Do not invent references, experiments, "
        "numbers, quotations, study findings or citation markers: the host adds citations. A block may "
        "express a normative position or hypothesis without empirical evidence if labeled accordingly. "
        "For facts based on abstracts, search results, unchecked excerpts or unverified source attribution, "
        "qualification must explicitly disclose that limit IN ENGLISH. Keep claim scope modest and "
        "the strongest counterargument visible. The evidence is provisional, not human-verified."
    ),
    "review": (
        "Act as a critical reviewer in a fresh context. You receive the manuscript, source excerpts, "
        "reading/search scope, and prior issues/responses, not the writer's private reasoning. Inspect "
        "novelty, fairness to prior work, citation support, logical leaps, strongest counterexamples, "
        "scope and venue contribution. Inspect verification.coverage and facts for extraction omissions, "
        "compound assertions, numbers, populations, negation and causal leaps. A citation on a paragraph "
        "does not establish support for each assertion. Target exact supplied block/draft/claim/evidence/paper IDs. "
        "Each criticism needs a reason and suggested repair, not a score. "
        "Check that block kinds are honest: labeling an empirical assertion as a position must not evade evidence requirements. "
        "Distinguish lack of evidence from evidence against the claim. Do not close a prior issue merely because wording changed. "
        "Explicitly reassess every unresolved critical/major prior issue in prior_issue_assessments. "
        "Explain substantive repairs or why a changed thesis makes the issue not applicable; preserve "
        "uncertainty. A persisting major issue prevents ready_for_human. "
        "ready_for_human requires supported fact judgments and complete extraction coverage, with no critical or major unaddressed issue; it is not publication approval."
    ),
    "redesign": (
        "Respond to the critical review by changing research, not just polishing language. If the problem "
        "is missing literature, choose research and specific new searches; if the thesis is too strong, "
        "revise its scope and premises; fork/merge if another position is needed; withdraw if the contribution "
        "collapses; hold if it needs human judgment. Record responses and remaining uncertainty for "
        "all supplied blocking issues. Use only given evidence IDs, never disputed/inaccessible evidence. "
        "A revised thesis must actually differ from the old one. Do not assert that a repair is proven; "
        "new drafts will be reviewed again."
    ),
    "decision": (
        "Choose exactly one of the host's allowed choices using its choice_id. Explain how it reduces "
        "a specific uncertainty or addresses a research need. Do not continue work merely to produce "
        "a manuscript. Use a hold/consult choice for genuine lack of a viable path. Follow the host's "
        "gates and prefer strong counterevidence over cosmetic refinement."
    ),
}


def check_ids(values, allowed, name):
    if len(set(values)) != len(values) or not set(values) <= set(allowed):
        raise ResearchError(f"{name}に重複または入力に存在しないIDがあります。")


def blocks(draft):
    return draft["abstract_blocks"] + [block for section in draft["sections"] for block in section["blocks"]]


def validate_semantics(kind, data, result):
    context = data.get("context", {})
    evidences = context.get("evidence", {})
    claims = context.get("claims", {})
    papers = context.get("papers", {})
    if kind == "reading":
        for finding in result["findings"]:
            if finding["excerpt"] not in data["page_text"]:
                raise ResearchError("読解結果の引用が取得資料の本文に一致しません。")
    elif kind == "exploration":
        if len(result["candidates"]) == 1:
            raise ResearchError("比較できる複数の立場、または候補なしの判断を返してください。")
        if len({candidate["text"] for candidate in result["candidates"]}) != len(result["candidates"]):
            raise ResearchError("同じ主張が候補として重複しています。")
        for candidate in result["candidates"]:
            check_ids(candidate["supporting_evidence_ids"], evidences, "支持根拠")
            check_ids(candidate["opposing_evidence_ids"], evidences, "反対根拠")
            for overlap in candidate["overlaps"]:
                if overlap["paper_id"] not in papers:
                    raise ResearchError("競合研究が入力文献に存在しません。")
                levels = {"metadata": 0, "abstract": 1, "passages": 2}
                if levels[overlap["verification_scope"]] > levels[papers[overlap["paper_id"]]["seen_scope"]]:
                    raise ResearchError("競合研究の確認範囲を実際より強く扱っています。")
    elif kind == "comparison":
        assessed = [item["claim_id"] for item in result["assessments"]]
        check_ids(assessed, claims, "比較対象")
        if set(assessed) != set(claims):
            raise ResearchError("比較対象の候補をすべて評価してください。")
        selected = result["selected_claim_id"]
        if result["decision"] == "proceed":
            if selected not in claims:
                raise ResearchError("選んだ主張が比較対象にありません。")
            if not any(e["claim_id"] == selected and e["document_source"] for e in evidences.values()):
                raise ResearchError("選んだ立場に取得資料と結び付く根拠がありません。")
        elif selected:
            raise ResearchError("保留・撤回時に主張を採用できません。")
    elif kind == "outline":
        if result["central_claim_id"] != data["claim_id"]:
            raise ResearchError("論証の中心主張が選択された立場と一致しません。")
        for section in result["sections"]:
            for premise in section["premises"]:
                check_ids(premise["evidence_ids"], evidences, "論証の根拠")
                if premise["kind"] == "descriptive" and not premise["evidence_ids"]:
                    raise ResearchError("事実を述べる前提に根拠がありません。")
            for objection in section["counterarguments"]:
                check_ids(objection["evidence_ids"], evidences, "反論の根拠")
    elif kind == "draft":
        required_roles = {"introduction", "related_work", "position", "counterarguments",
                          "implications", "limitations", "conclusion"}
        if {section["role"] for section in result["sections"]} != required_roles:
            raise ResearchError("ポジションペーパーに必要な論証の構成が揃っていません。")
        for block in blocks(result):
            check_ids(block["evidence_ids"], evidences, "草稿の引用")
            check_ids(block["claim_ids"], [data["claim_id"]], "草稿の主張")
            if not block["claim_ids"]:
                raise ResearchError("草稿の主要な記述と主張が結び付いていません。")
            if re.search(r"\[(?:E\d+|P\d+|\d+)\]", block["text"]):
                raise ResearchError("草稿本文にモデルが独自に挿入した引用記号があります。")
            if block["kind"] == "fact":
                if not block["evidence_ids"]:
                    raise ResearchError("根拠のない事実の断定が草稿にあります。")
                limited = any(
                    evidences[eid]["level"] != "passage" or evidences[eid]["status"] != "checked"
                    or not evidences[eid]["document_source"]
                    for eid in block["evidence_ids"]
                )
                if limited and not block["qualification"].strip():
                    raise ResearchError("限定的・未確認の根拠に基づく事実に留保がありません。")
    elif kind == "review":
        from .verification import blocking
        if result["decision"] == "ready_for_human" and blocking(data["verification"]["content"]):
            raise ResearchError("未支持の事実主張や抽出不足を残したまま人間確認へ進めません。")
        targets = {"draft": {data["draft"]["id"]}, "block": {b["id"] for b in blocks(data["draft"]["content"])},
                   "claim": set(claims), "evidence": set(evidences), "paper": set(papers)}
        for finding in result["findings"]:
            if finding["target_id"] not in targets[finding["target_type"]]:
                raise ResearchError("査読の指摘先が入力に存在しません。")
            check_ids(finding["evidence_ids"], evidences, "査読の根拠")
        if result["decision"] == "ready_for_human" and any(f["severity"] in ("critical", "major") for f in result["findings"]):
            raise ResearchError("重大な指摘を残したまま、人間確認へ進めません。")
        if any(f["suggested_action"] == "search" for f in result["findings"]) and not result["requested_searches"]:
            raise ResearchError("文献調査への差し戻しに検索方針がありません。")
        prior = {issue["id"]: issue for issue in data["prior_issues"]}
        assessments = [item["issue_id"] for item in result["prior_issue_assessments"]]
        check_ids(assessments, prior, "以前の指摘の再確認")
        expected = {iid for iid, issue in prior.items() if issue["severity"] in ("critical", "major")
                    and issue["status"] not in ("addressed", "rejected", "not_applicable")}
        if not expected <= set(assessments):
            raise ResearchError("以前の重大な指摘を再確認していません。")
        if result["decision"] == "ready_for_human" and any(
            item["status"] == "persists" and prior[item["issue_id"]]["severity"] in ("critical", "major")
            for item in result["prior_issue_assessments"]
        ):
            raise ResearchError("以前の重大な問題が残ったまま、人間確認へ進めません。")
    elif kind == "redesign":
        check_ids(result["evidence_ids"], evidences, "再設計の根拠")
        check_ids(result["parent_claim_ids"], claims, "統合元の主張")
        expected = {issue["id"] for issue in data["issues"] if issue["severity"] in ("critical", "major")}
        responses = [response["issue_id"] for response in result["issue_responses"]]
        check_ids(responses, [issue["id"] for issue in data["issues"]], "指摘への応答")
        if not expected <= set(responses):
            raise ResearchError("重大な指摘に応答していません。")
        if result["decision"] in ("revise", "fork", "merge"):
            if not result["text"].strip() or result["text"].strip() == claims[data["claim_id"]]["text"].strip():
                raise ResearchError("主張の再設計が実際の変更になっていません。")
        if result["decision"] == "merge" and len(result["parent_claim_ids"]) < 2:
            raise ResearchError("統合には複数の主張が必要です。")
        if result["decision"] == "research" and not result["requested_searches"]:
            raise ResearchError("再調査の具体的な検索方針がありません。")
    elif kind == "decision":
        if result["choice_id"] not in {choice["choice_id"] for choice in data["choices"]}:
            raise ResearchError("許可されていない次の作業が選ばれました。")
    return result


PROMPT_VERSIONS = {kind: 2 for kind in PROMPTS}

from .verification import PROMPT as VERIFY_PROMPT
PROMPTS["verify"] = VERIFY_PROMPT
PROMPT_VERSIONS["verify"] = 2
