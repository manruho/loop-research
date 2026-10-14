"""OpenAlex discovery: bounded, reproducible metadata and citation neighborhoods."""
import json
import os
import re
import urllib.parse
from .common import ResearchError, canonical_text, identifier, label
from .literature import get_bytes, http_url, MAX_METADATA

BASE = "https://api.openalex.org/works"


def headers():
    key = os.environ.get("OPENALEX_API_KEY")
    return {"Authorization": "Bearer " + key} if key else {}


def request(url):
    # Only metadata endpoints. Credentials never enter recorded request URLs.
    payload, _ = get_bytes(url, MAX_METADATA, headers=headers(), retries=2, rate_slot="openalex")
    try:
        result = json.loads(payload)
    except (ValueError, UnicodeError) as exc:
        raise ResearchError("OpenAlex応答を読み取れません。") from exc
    if not isinstance(result, dict) or "error" in result:
        raise ResearchError("OpenAlexが正常な書誌情報を返しませんでした。")
    return result


def parse_work(work):
    if not isinstance(work, dict) or not isinstance(work.get("id"), str) or not re.fullmatch(r"https://openalex.org/W[1-9][0-9]*", work["id"]):
        raise ResearchError("OpenAlex文献IDが不正です。")
    raw_title = work.get("title") or work.get("display_name") or ""
    if not isinstance(raw_title, str):
        raise ResearchError("OpenAlex文献タイトルの形式が不正です。")
    title = canonical_text(raw_title)
    if not title:
        raise ResearchError("OpenAlex文献のタイトルがありません。")
    doi_url = work.get("doi") or ""
    if not isinstance(doi_url, str) or (doi_url and not doi_url.startswith(("https://doi.org/", "http://doi.org/"))):
        raise ResearchError("OpenAlex DOIの形式が不正です。")
    doi = doi_url.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
    abstract = work.get("abstract_inverted_index") or {}
    if not isinstance(abstract, dict):
        raise ResearchError("OpenAlexの抄録形式が不正です。")
    positions = {}
    for word, values in abstract.items():
        if not isinstance(word, str) or not isinstance(values, list) or any(type(p) is not int or p < 0 for p in values):
            raise ResearchError("OpenAlex抄録の位置が不正です。")
        for position in values:
            if position in positions and positions[position] != word:
                raise ResearchError("OpenAlex抄録の位置が衝突しています。")
            positions[position] = word
    text = " ".join(positions[p] for p in sorted(positions))
    location = work.get("best_oa_location") or work.get("primary_location") or {}
    if not isinstance(location, dict) or not isinstance(work.get("authorships", []), list):
        raise ResearchError("OpenAlexの所在・著者情報が不正です。")
    if any(not isinstance(a, dict) or not isinstance(a.get("author", {}), dict) or not isinstance(a.get("author", {}).get("display_name", ""), str) for a in work.get("authorships", [])):
        raise ResearchError("OpenAlexの著者形式が不正です。")
    pdf = location.get("pdf_url") or ""
    url = location.get("landing_page_url") or doi_url or work["id"]
    return {"canonical_key": "doi:" + doi.lower() if doi else "openalex:" + work["id"].rsplit("/", 1)[-1],
            "provider": "openalex", "external_id": work["id"].rsplit("/", 1)[-1], "title": title,
            "authors": [a.get("author", {}).get("display_name", "") for a in work.get("authorships", [])],
            "doi": doi, "url": http_url(url), "pdf_url": http_url(pdf) if pdf else "",
            "source_version": location.get("version") or "", "published": work.get("publication_date") or "",
            "abstract": text, "metadata": work}


def search_openalex(store, query, limit):
    url = BASE + "?" + urllib.parse.urlencode({"search": query, "per_page": limit})
    sid = store.start_search("openalex", query, limit, url)
    try:
        result = request(url)
        if not isinstance(result.get("results"), list):
            raise ResearchError("OpenAlexの検索応答に文献一覧がありません。")
        papers = [parse_work(w) for w in result["results"]]
        return store.finish_search(sid, papers, response=result)
    except KeyboardInterrupt:
        store.finish_search(sid, error="検索を中断しました。", interrupted=True)
        raise
    except (ResearchError, ValueError, TypeError, KeyError) as exc:
        store.finish_search(sid, error=str(exc))
        raise ResearchError(str(exc)) from exc


def resolve(store, paper):
    row = store.paper_row(paper)
    alias = store.db.execute("SELECT external_id FROM paper_aliases WHERE provider='openalex' AND paper_id=?", (row["id"],)).fetchone()
    if alias:
        work = request(BASE + "/" + alias[0])
    elif row["provider"] == "openalex":
        work = request(BASE + "/" + row["external_id"])
    elif row["doi"]:
        work = request(BASE + "/doi:" + urllib.parse.quote(row["doi"], safe="/"))
    elif row["external_id"] and re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v[1-9]\d*)?", row["external_id"]):
        base_id = re.sub(r"v[0-9]+$", "", row["external_id"])
        # A repository identifier is not necessarily indexed as a DOI. Search is
        # only a candidate lookup: exact repository identity must be in locations.
        response = request(BASE + "?" + urllib.parse.urlencode({"search": row["title"], "per_page": 5}))
        candidates = []
        for candidate in response.get("results", []):
            if not isinstance(candidate, dict):
                continue
            locations = candidate.get("locations") or []
            if not isinstance(locations, list):
                continue
            for location in locations:
                if not isinstance(location, dict):
                    continue
                urls = [location.get("landing_page_url"), location.get("pdf_url")]
                if any(arxiv_identity(url) == base_id for url in urls):
                    candidates.append(candidate)
                    break
        matches = {candidate.get("id"): candidate for candidate in candidates}
        if len(matches) != 1:
            raise ResearchError("OpenAlexでarXiv識別子に一致する文献を一意に確認できません。タイトル類似だけでは統合しません。")
        work = next(iter(matches.values()))
    else:
        # No fuzzy title merge. User can supply an exact OpenAlex ID via paper add.
        raise ResearchError("引用探索にはDOIまたはOpenAlex IDが必要です。タイトル類似で自動統合しません。")
    parsed = parse_work(work)
    known_dois = {parsed["doi"].lower()}
    for location in work.get("locations") or []:
        if isinstance(location, dict) and isinstance(location.get("doi"), str):
            known_dois.add(location["doi"].removeprefix("https://doi.org/").removeprefix("http://doi.org/").lower())
    if row["doi"] and row["doi"].lower() not in known_dois:
        raise ResearchError("OpenAlexの識別子が登録文献と一致しません。")
    return work


def arxiv_identity(url):
    if not isinstance(url, str):
        return None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or parsed.hostname not in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
        return None
    match = re.fullmatch(r"/(?:abs|pdf)/((?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7}))(?:v[1-9]\d*)?(?:\.pdf)?", parsed.path)
    return match[1] if match else None


def related_papers(store, paper, direction="both", limit=10, remaining=30):
    if direction not in ("references", "citations", "both") or not 1 <= limit <= 20 or remaining < 0:
        raise ResearchError("引用探索の方向・上限が不正です。")
    outputs = []
    for side in (("references", "citations") if direction == "both" else (direction,)):
        query = "graph:" + paper + ":" + side
        sid = store.start_search("openalex", query, limit, BASE)
        try:
            work = resolve(store, paper)
            wid = work["id"].rsplit("/", 1)[-1]
            if side == "references":
                ids = list(dict.fromkeys(work.get("referenced_works", [])))[:limit]
                url = BASE + "?" + urllib.parse.urlencode({"filter": "openalex:" + "|".join(ids), "per_page": limit}) if ids else BASE
                result = request(url) if ids else {"results": []}
                result["_coverage"] = {"available": len(work.get("referenced_works", [])), "requested": len(ids), "truncated": len(work.get("referenced_works", [])) > limit}
            else:
                url = BASE + "?" + urllib.parse.urlencode({"filter": "cites:" + wid, "per_page": limit, "sort": "publication_date:desc"})
                result = request(url)
                result["_coverage"] = {"available": result.get("meta", {}).get("count"), "requested": limit,
                                       "truncated": result.get("meta", {}).get("count", 0) > limit}
            result["_seed"] = {"id": work["id"], "direction": side, "request_url": url,
                               "scope": "provider-reported links; not read or support-verified"}
            if not isinstance(result.get("results"), list):
                raise ResearchError("OpenAlexの引用応答に文献一覧がありません。")
            parsed = [parse_work(w) for w in result["results"]]
            existing = {r[0] for r in store.db.execute("SELECT canonical_key FROM papers")}
            accepted = []
            for item in parsed:
                if item["canonical_key"] not in existing:
                    if remaining <= 0:
                        continue
                    remaining -= 1
                    existing.add(item["canonical_key"])
                accepted.append(item)
            result["_coverage"]["budget_omitted"] = len(parsed) - len(accepted)
            with store.db:
                seed = identifier(paper, "P")
                store.db.execute("INSERT OR IGNORE INTO paper_aliases VALUES('openalex',?,?)", (wid, seed))
                for item in accepted:
                    pid = store._insert_paper(item)
                    if pid != seed:
                        citing, cited = (seed, pid) if side == "references" else (pid, seed)
                        store.db.execute("INSERT OR IGNORE INTO citation_edges VALUES(?,?,?)", (sid, citing, cited))
                outputs.append(store.finish_search(sid, accepted, response=result))
        except KeyboardInterrupt:
            store.finish_search(sid, error="引用探索を中断しました。", interrupted=True)
            raise
        except (ResearchError, ValueError, TypeError, KeyError) as exc:
            store.finish_search(sid, error=str(exc))
            raise ResearchError(str(exc)) from exc
    return outputs
