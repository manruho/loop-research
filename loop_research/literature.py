"""Public metadata discovery and bounded PDF/text ingestion. No LLM API needed."""

import contextlib
import json
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path

from .common import ResearchError, canonical_text, label, required


MAX_METADATA = 4 * 1024 * 1024
MAX_DOCUMENT = 20 * 1024 * 1024
USER_AGENT = "loop-research/0.3 (local academic research; https://github.com/manruho/loop-research)"
ARXIV_ID = re.compile(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})(?:v[1-9]\d*)?\Z")


def http_url(url):
    parts = urllib.parse.urlsplit(required(url, "URL"))
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ResearchError("資格情報を含まないHTTP/HTTPSのURLを指定してください。")
    return url


@contextlib.contextmanager
def arxiv_api_slot():
    # Serialize API calls across local CLI invocations, including failed requests.
    try:
        import fcntl
    except ImportError as exc:
        raise ResearchError("arXivのAPI呼び出しは現在Unix環境に対応しています。") from exc
    path = Path.home() / ".cache" / "loop-research" / "arxiv-api.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        try:
            last = float(handle.read() or "0")
        except ValueError:
            last = 0
        wait = max(0, min(3, 3 - (time.time() - last)))
        if wait:
            time.sleep(wait)
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            handle.write(str(time.time()))
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_UN)


def get_bytes(url, maximum, timeout=25, arxiv_api=False, headers=None, retries=0, rate_slot=None):
    http_url(url)
    if rate_slot:
        with provider_slot(rate_slot):
            return get_bytes(url, maximum, timeout, arxiv_api, headers, retries)
    slot = arxiv_api_slot() if arxiv_api else contextlib.nullcontext()
    try:
        with slot:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})})
            with urllib.request.build_opener(SafeRedirect()).open(request, timeout=timeout) as response:
                final_url = http_url(response.geturl())
                payload = response.read(maximum + 1)
                if len(payload) > maximum:
                    raise ResearchError(f"取得資料が上限{maximum // (1024 * 1024)}MBを超えています。")
                return payload, final_url
    except urllib.error.HTTPError as exc:
        if retries and exc.code in (429, 500, 502, 503, 504):
            delay = exc.headers.get("Retry-After", "2") if exc.headers else "2"
            try:
                delay = min(10, max(1, float(delay)))
            except ValueError:
                delay = 2
            time.sleep(delay)
            return get_bytes(url, maximum, timeout, arxiv_api, headers, retries-1)
        raise ResearchError(f"取得先がHTTP {exc.code}を返しました。アクセス制限や一時的な障害の可能性があります。") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        raise ResearchError("取得先へ接続できませんでした。通信状態を確認して再実行してください。") from exc


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = super().redirect_request(req, fp, code, msg, headers, newurl)
        if target is not None and urllib.parse.urlsplit(req.full_url).hostname != urllib.parse.urlsplit(newurl).hostname:
            target.remove_header("Authorization")
        return target


class PlainMarkup(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_markup(value):
    parser = PlainMarkup()
    parser.feed(value)
    return canonical_text(" ".join(parser.parts))


def parse_arxiv(payload):
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ResearchError("arXivの応答をAtom XMLとして読めません。") from exc
    ns = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}
    if root.tag != "{http://www.w3.org/2005/Atom}feed":
        raise ResearchError("arXivの応答がAtomフィードではありません。")
    papers = []
    for entry in root.findall("a:entry", ns):
        eid = entry.findtext("a:id", "", ns).rsplit("/abs/", 1)[-1]
        title = canonical_text(entry.findtext("a:title", "", ns))
        if not ARXIV_ID.fullmatch(eid) or title.lower() == "error":
            raise ResearchError("arXivが検索エラーまたは不正な識別子を返しました。")
        version = re.search(r"v\d+$", eid)
        papers.append({
            "canonical_key": "arxiv:" + eid, "provider": "arxiv", "external_id": eid,
            "title": required(title, "文献名"), "authors": [canonical_text(a.findtext("a:name", "", ns)) for a in entry.findall("a:author", ns)],
            "doi": entry.findtext("x:doi", "", ns), "url": "https://arxiv.org/abs/" + eid,
            "pdf_url": "https://arxiv.org/pdf/" + eid, "source_version": version.group() if version else "",
            "published": entry.findtext("a:published", "", ns), "abstract": entry.findtext("a:summary", "", ns),
            "metadata": {"entry_xml": ET.tostring(entry, encoding="unicode")},
        })
    return papers


def parse_crossref(payload):
    try:
        data = json.loads(payload)
        items = data["message"]["items"]
        if not isinstance(items, list):
            raise TypeError
        papers = []
        for item in items:
            doi = required(item["DOI"], "DOI")
            title = required(canonical_text(item["title"][0]), "文献名")
            parts = item.get("issued", {}).get("date-parts", [[]])[0]
            authors = [canonical_text(a.get("given", "") + " " + a.get("family", a.get("name", ""))) for a in item.get("author", [])]
            pdf = next((link["URL"] for link in item.get("link", []) if link.get("content-type") == "application/pdf" and link.get("URL")), "")
            papers.append({
                "canonical_key": "doi:" + doi.lower(), "provider": "crossref", "external_id": doi,
                "title": title, "authors": authors, "doi": doi,
                "url": item.get("URL") or "https://doi.org/" + doi,
                "pdf_url": pdf, "source_version": "", "published": "-".join(str(p) for p in parts),
                "abstract": plain_markup(item.get("abstract", "")), "metadata": item,
            })
        return papers, data
    except (json.JSONDecodeError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ResearchError("Crossrefの書誌応答を読み取れません。") from exc


def arxiv_expression(query):
    try:
        lexer = shlex.shlex(query, posix=True, punctuation_chars="()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError as exc:
        raise ResearchError("arXiv検索語の引用符が閉じられていません。") from exc
    tokens = [part for token in tokens for part in (list(token) if token and set(token) <= {"(", ")"} else [token])]
    operators = {"AND", "OR", "ANDNOT"}
    fields = {"ti", "au", "abs", "co", "jr", "cat", "rn", "id", "all"}
    advanced = any(token in operators or token in ("(", ")")
                   or (":" in token and token.split(":", 1)[0] in fields) for token in tokens)
    if not advanced:
        phrase = " ".join(tokens).replace('"', " ")
        return 'all:"' + canonical_text(phrase) + '"'
    expression = []
    for token in tokens:
        if token in operators or token in ("(", ")"):
            expression.append(token)
        else:
            prefix, _, value = token.partition(":")
            if prefix not in fields:
                prefix, value = "all", token
            expression.append(prefix + ':"' + value.replace('"', " ") + '"')
    return " ".join(expression)


def search_papers(store, query, provider="crossref", limit=5):
    query = required(query, "検索語")
    if not 1 <= limit <= 20:
        raise ResearchError("検索件数は1〜20件で指定してください。")
    if provider == "openalex":
        from .discovery import search_openalex
        return search_openalex(store, query, limit)
    if provider == "crossref":
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode({"query.bibliographic": query, "rows": limit})
    elif provider == "arxiv":
        url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode({"search_query": arxiv_expression(query), "start": 0, "max_results": limit, "sortBy": "relevance"})
    else:
        raise ResearchError("検索先はcrossref / arxiv / openalexを指定してください。")
    sid = store.start_search(provider, query, limit, url)
    try:
        payload, _ = get_bytes(url, MAX_METADATA, arxiv_api=provider == "arxiv")
        if provider == "arxiv":
            papers = parse_arxiv(payload)
            response = {"atom_xml": payload.decode("utf-8")}
        else:
            papers, response = parse_crossref(payload)
        return store.finish_search(sid, papers=papers, response=response)
    except KeyboardInterrupt:
        store.finish_search(sid, error="利用者が文献探索を中断しました。", interrupted=True)
        raise ResearchError(f"{label(sid, 'S')}: 文献探索の中断を記録しました。")
    except (ResearchError, OSError, UnicodeError) as exc:
        store.finish_search(sid, error=str(exc))
        raise ResearchError(f"{label(sid, 'S')}: {exc}") from exc


def register_paper(store, title=None, url=None, pdf_url=None, arxiv=None, source_version=""):
    if arxiv:
        if not ARXIV_ID.fullmatch(arxiv):
            raise ResearchError("arXiv IDは2408.06292v3のように指定してください。")
        if url or pdf_url or source_version:
            raise ResearchError("--arxivとURL・版の指定は併用できません。")
        version = re.search(r"v\d+$", arxiv)
        data = {"canonical_key": "arxiv:" + arxiv, "provider": "manual", "external_id": arxiv,
                "title": title or f"arXiv:{arxiv}（書誌未確認）", "url": "https://arxiv.org/abs/" + arxiv,
                "pdf_url": "https://arxiv.org/pdf/" + arxiv, "source_version": version.group() if version else "",
                "metadata": {"registration": "user supplied arxiv identifier; bibliographic metadata not verified"}}
    else:
        url = http_url(url)
        data = {"canonical_key": "manual:" + url + "#" + source_version, "provider": "manual", "external_id": url,
                "title": required(title, "文献名"), "url": url, "pdf_url": http_url(pdf_url) if pdf_url else "",
                "source_version": source_version, "metadata": {"registration": "user supplied metadata"}}
    return store.add_paper(data)


def extract_document(content):
    if content.startswith(b"%PDF-"):
        binary = shutil.which("pdftotext")
        if binary is None:
            raise ResearchError("PDF抽出にはpdftotextが必要です。Ubuntuでは sudo apt install poppler-utils で導入できます。")
        with tempfile.TemporaryDirectory(prefix="loop-research-pdf-") as directory:
            path = Path(directory) / "source.pdf"
            path.write_bytes(content)
            try:
                # Poppler's default reading order keeps two-column sentences contiguous.
                # Flattening -layout output would incorrectly interleave separate columns.
                result = subprocess.run([binary, "-enc", "UTF-8", str(path), "-"], capture_output=True, timeout=45)
            except subprocess.TimeoutExpired as exc:
                raise ResearchError("PDFの本文抽出が制限時間を超えました。") from exc
            if result.returncode != 0:
                raise ResearchError("PDFの本文を抽出できません。暗号化や破損の可能性があります。")
            if len(result.stdout) > MAX_DOCUMENT:
                raise ResearchError("抽出本文が大きすぎます。")
            pages = result.stdout.decode("utf-8").split("\f")
            if pages and not pages[-1].strip():
                pages.pop()
        if not any(canonical_text(page) for page in pages):
            raise ResearchError("PDFに抽出可能な本文がありません。画像PDFのOCRは未対応です。")
        return "pdf", pages, "poppler-pdftotext-reading-order-v1; whitespace-normalized"
    try:
        text = content.decode("utf-8-sig")
    except UnicodeError as exc:
        raise ResearchError("資料はPDFまたはUTF-8のテキストに対応しています。") from exc
    if "\x00" in text or not text.strip():
        raise ResearchError("資料が空、または対応しないバイナリ形式です。")
    if re.search(r"<!doctype\s+html|<html\b", text[:2000], re.IGNORECASE):
        raise ResearchError("HTMLページを論文本文として取り込めません。PDFまたは本文テキストを指定してください。")
    return "text", [text], "utf8-text; whitespace-normalized"


def fetch_paper(store, paper, url=None, file=None, abstract=False):
    metadata = store.paper_row(paper)
    if sum(bool(x) for x in (url, file, abstract)) > 1:
        raise ResearchError("URL、ローカルファイル、抄録の取得は同時に指定できません。")
    requested = str(Path(file).expanduser().resolve()) if file else metadata["url"] if abstract else url or metadata["pdf_url"]
    fid = store.start_fetch(paper, requested)
    content = None
    final_url = requested
    origin = "abstract" if abstract else "pdf"
    try:
        if abstract:
            required(metadata["abstract"], "検索時に取得した抄録（本文の取得またはローカル資料の登録を利用してください）")
            content = metadata["metadata_json"].encode("utf-8")
            pages, extractor = [metadata["abstract"]], "provider-metadata-abstract; whitespace-normalized"
        else:
            if file:
                path = Path(file).expanduser().resolve()
                if path.suffix.lower() not in (".pdf", ".txt", ".md"):
                    raise ResearchError("ローカル資料は.pdf / .txt / .mdに対応しています。")
                with path.open("rb") as handle:
                    content = handle.read(MAX_DOCUMENT + 1)
                if len(content) > MAX_DOCUMENT:
                    raise ResearchError("資料が20MBを超えています。")
                final_url = path.as_uri()
                origin = "pdf" if content.startswith(b"%PDF-") else "text"
                if path.suffix.lower() == ".pdf" and origin != "pdf":
                    raise ResearchError("指定ファイルはPDF形式ではありません。")
            else:
                if not requested:
                    raise ResearchError("PDFの取得先が未確認です。--urlでPDFのURL、または--fileで入手した資料を指定してください。")
                content, final_url = get_bytes(requested, MAX_DOCUMENT)
                if not content.startswith(b"%PDF-"):
                    # Do not turn publisher landing pages or access-denied HTML into evidence.
                    content = None
                    raise ResearchError("取得先はPDFを返していません。本文として扱わず取得失敗を記録します。")
            origin, pages, extractor = extract_document(content)
        return store.save_document(fid, paper, content, origin, final_url, pages, extractor)
    except KeyboardInterrupt:
        store.fail_fetch(fid, "利用者が文献の取得・抽出を中断しました。", interrupted=True)
        raise ResearchError(f"{label(fid, 'F')}: 中断を記録しました。")
    except (ResearchError, OSError, UnicodeError) as exc:
        if content is not None and origin == "pdf" and content.startswith(b"%PDF-"):
            store.save_document(fid, paper, content, origin, final_url, [], "extraction-failed", error=str(exc))
        else:
            store.fail_fetch(fid, str(exc))
        raise ResearchError(f"{label(fid, 'F')}: {exc}") from exc


@contextlib.contextmanager
def provider_slot(provider):
    import fcntl
    path = Path.home() / ".cache" / "loop-research" / (provider + "-api.lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.seek(0)
        try:
            last = float(handle.read() or "0")
        except ValueError:
            last = 0
        time.sleep(max(0, min(1, 1 - (time.time() - last))))
        try:
            yield
        finally:
            handle.seek(0)
            handle.truncate()
            handle.write(str(time.time()))
            handle.flush()
            fcntl.flock(handle, fcntl.LOCK_UN)
