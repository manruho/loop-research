"""Exploratory paired evaluations. Human judgments are never fabricated."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import random
import re
import signal
import subprocess
import time

from .common import ResearchError, now
from .codex import codex_status, subscription_environment, stop_process
from .engine import CodexEngine, diagnostic, guard_parent
from .schema import obj, array, string, validate

FACT_EXTRACTION = obj(facts=array(string(minimum=1, maximum=1800), maximum=150),
                      limitations=array(string(minimum=1), minimum=1, maximum=12))
RUBRIC = ("prior_work", "argument", "counterarguments", "qualification", "traceability")


def read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, UnicodeError, ValueError) as exc:
        raise ResearchError("評価ファイルを読み取れません。UTF-8 JSONを指定してください。") from exc


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def load_cases(path):
    spec = read(path)
    cases = spec.get("cases", [])
    if len(cases) != 3 or len({c["id"] for c in cases}) != 3:
        raise ResearchError("評価には重複しない公開課題3件が必要です。")
    for case in cases:
        if not re.fullmatch(r"[a-z0-9-]+", case["id"]) or not case["question"].strip():
            raise ResearchError("評価課題のID・問いが不正です。")
    return spec


def baseline(case, root, model, seconds):
    auth = codex_status()
    if not auth["chatgpt_authenticated"]:
        raise ResearchError(auth["message"])
    seed = case["seed"]
    prompt = ("Research this position-paper question and write an English working manuscript of 1200-2000 words, "
              "with verifiable references, strongest counterarguments, limits, search/reading scope and uncertainties. "
              "Distinguish normative premises from empirical findings. You may hold or withdraw instead of forcing a paper. "
              "Use public literature and do not invent citations. Search and read as needed.\nQUESTION: " + case["question"]
              + "\nCOMMON STARTING SOURCE: " + seed["title"] + " https://arxiv.org/abs/" + seed["arxiv"]
              + "\nPut the complete manuscript in your final response. If holding/withdrawing without a manuscript, start with NO_PAPER: hold or NO_PAPER: withdraw followed by reasons. Do not modify the project.")
    (root / "prompt.txt").write_text(prompt)
    command = ["codex", "--no-daemon", "--search", "-a", "never", "exec", "--ignore-user-config", "--ephemeral",
               "--skip-git-repo-check", "--sandbox", "workspace-write", "--json", "--model", model,
               "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"',
               "-c", 'model_reasoning_effort="medium"', "-c", "features.unbounded_connection_retries=false",
               "-c", "features.multi_agent=false", "-c", "features.apps=false", "-c", "features.plugins=false",
               "--output-last-message", str((root / "draft.md").resolve()), "-"]
    start = time.monotonic()
    parent = os.getpid()
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=root, env=subscription_environment(), start_new_session=True,
                            preexec_fn=(lambda: guard_parent(parent)) if __import__('sys').platform.startswith('linux') else None)
    try:
        stdout, stderr = proc.communicate(prompt, timeout=seconds)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        stop_process(proc)
        raise
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
            if isinstance(event, dict):
                events.append(event)
        except ValueError:
            continue
    # Don't persist tool output, reasoning, or potentially sensitive environment dumps.
    usages = [e.get("usage") for e in events if e.get("type") == "turn.completed"]
    completed = proc.returncode == 0 and bool(usages) and not any(e.get("type") in ("turn.failed", "error") for e in events)
    draft_file = root / "draft.md"
    has_manuscript = draft_file.exists() and not draft_file.read_text().lstrip().startswith("NO_PAPER:")
    no_paper = completed and not has_manuscript
    if no_paper and draft_file.exists():
        draft_file.rename(root / "no-paper.txt")
    return {"status": "held" if no_paper else "completed" if completed else "failed", "manuscript_available": completed and has_manuscript, "elapsed_seconds": time.monotonic()-start,
            "usage": usages, "tool_calls": sum(e.get("type") == "item.completed" and e.get("item", {}).get("type") in ("web_search", "command_execution") for e in events),
            "cli_version": auth.get("version"), "requested_model": model,
            "diagnostic": diagnostic(stderr) if not completed else None,
            "scope": "Normal Codex with web search and workspace-write in isolated evaluation directory"}


def harness(case, root, model, review_model, seconds):
    from .store import ResearchStore
    from .literature import register_paper
    from .workflow import ResearchWorkflow
    from .export import export_study
    store = ResearchStore(root / "study.sqlite3", initialize=True)
    start = time.monotonic()
    try:
        store.init(case["question"])
        store.configure_study("Predeclared pilot models", settings={"model": model, "review_model": review_model, "timeout": 240})
        register_paper(store, title=case["seed"]["title"], arxiv=case["seed"]["arxiv"])
        result = ResearchWorkflow(store).run(200, max_seconds=seconds)
        exported = export_study(store, root / "export")
        if result["current_draft"]:
            (root / "draft.md").write_text((root / "export" / "draft.md").read_text())
        else:
            (root / "no-paper.txt").write_text(result["state"]["reason"])
        tasks = [store.show_task(t["id"]) for t in store.list_tasks()]
        return {"status": result["state"]["status"], "elapsed_seconds": time.monotonic()-start,
                "usage": [t["execution"].get("usage") for t in tasks if t["execution"]],
                "task_count": len(tasks), "papers": len(store.list_papers()),
                "searches": len(store.list_searches()), "reading_scope": result["scope"],
                "current_draft": result["current_draft"], "reason": result["state"]["reason"],
                "requested_model": model, "requested_review_model": review_model,
                "scope": "Host discovery and bounded Codex roles; not tool-identical to baseline"}
    finally:
        store.close()


def run_evaluation(output, cases_path, model, review_model, seconds=1800, resume=False, methods=None):
    from .workflow import study_lock
    if type(seconds) is not int or not 1 <= seconds <= 7200:
        raise ResearchError("評価の時間上限は1〜7200秒です。")
    if not model or not review_model or model == review_model:
        raise ResearchError("評価にも異なる執筆・査読モデルを明示してください。")
    spec = load_cases(cases_path)
    root = Path(output).expanduser().resolve()
    spec_hash = hashlib.sha256(Path(cases_path).read_bytes()).hexdigest()
    if root.exists() and not resume:
        raise ResearchError("評価出力先は既存です。再開は--resumeを指定してください。")
    root.mkdir(parents=True, exist_ok=True)
    class Lock:
        path = root / "evaluation"
    with study_lock(Lock()):
        manifest_path = root / "manifest.json"
        manifest = read(manifest_path) if manifest_path.exists() else {
            "created_at": now(), "cases": spec, "cases_sha256": spec_hash, "model": model, "review_model": review_model,
            "seconds_per_run": seconds, "runs": [], "human_evaluation": "pending",
            "limitations": ["3 cases, 1 run per method; no statistical or general superiority claim.",
                             "Different tools and aggregate model usage; model diversity is a confound.",
                             "Reference-set membership and AI extraction are not human ground truth."]}
        if (manifest["model"], manifest["review_model"], manifest["cases_sha256"], manifest["seconds_per_run"]) != (model, review_model, spec_hash, seconds):
            raise ResearchError("再開のモデル・課題・予算が評価開始時と違います。")
        save(manifest_path, manifest)
        for case in spec["cases"]:
            # Counterbalance method order across cases, predeclared before outcomes.
            order = ("baseline", "harness") if spec["cases"].index(case) % 2 == 0 else ("harness", "baseline")
            for method in order:
                if methods and method not in methods:
                    continue
                if any(r["case_id"] == case["id"] and r["method"] == method for r in manifest["runs"]):
                    continue
                destination = root / (case["id"] + "-" + method)
                if destination.exists():
                    database = destination / "study.sqlite3"
                    if method == "harness" and database.exists():
                        from .store import ResearchStore
                        from .workflow import ResearchWorkflow
                        store = ResearchStore(database)
                        try:
                            ResearchWorkflow(store).recover()
                            if store.study_state()["status"] == "active":
                                store.control_study("interrupted", "評価制御プロセスの中断。結果は再実行せず保持。")
                        finally:
                            store.close()
                    # An interrupted attempt is evidence, not permission to silently re-run.
                    manifest["runs"].append({"case_id": case["id"], "method": method, "status": "interrupted", "path": destination.name,
                                             "reason": "Existing unfinished attempt retained; no automatic rerun"})
                    save(manifest_path, manifest)
                    continue
                destination.mkdir()
                source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob("*.py")}
                started = time.monotonic()
                print(case["id"] + " " + method + " started", flush=True)
                try:
                    result = (baseline(case, destination, model, seconds) if method == "baseline"
                              else harness(case, destination, model, review_model, seconds))
                except KeyboardInterrupt:
                    result = {"status": "interrupted", "elapsed_seconds": time.monotonic()-started}
                    manifest["runs"].append(dict(result, case_id=case["id"], method=method, path=destination.name, source_sha256=source_hashes))
                    save(manifest_path, manifest)
                    raise
                except (ResearchError, OSError, subprocess.TimeoutExpired) as exc:
                    result = {"status": "timed_out" if isinstance(exc, subprocess.TimeoutExpired) else "failed",
                              "reason": diagnostic(str(exc)) or type(exc).__name__, "elapsed_seconds": time.monotonic()-started}
                manifest["runs"].append(dict(result, case_id=case["id"], method=method, path=destination.name, source_sha256=source_hashes))
                save(manifest_path, manifest)
                print(case["id"] + " " + method + " " + result["status"], flush=True)
        return report(root)


def anonymize(text):
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = re.sub(r"\b[CEPDUBAV][0-9]{3,}\b", "[record]", text)
    text = re.sub(r"^>.*(?:historical|withdrawn|ready_for_human|finalized).*\n", "", text, flags=re.M|re.I)
    return text.strip() + "\n"


def packet(output, reviewer_model):
    root = Path(output).resolve()
    manifest = read(root / "manifest.json")
    target = root / "blind-packet"
    if target.exists():
        raise ResearchError("評価票は既存です。上書きしません。")
    stage = root / "packet-staging"
    stage.mkdir(exist_ok=False)
    mapping, samples = {}, []
    runs = list(manifest["runs"])
    random.Random(108).shuffle(runs)
    try:
        for index, run in enumerate(runs, 1):
            code = f"Sample-{index:02d}"
            mapping[code] = {"case_id": run["case_id"], "method": run["method"]}
            case = next(c for c in manifest["cases"]["cases"] if c["id"] == run["case_id"])
            draft = root / run["path"] / "draft.md"
            successful = draft.exists() and run["status"] not in ("failed", "timed_out", "interrupted")
            text = anonymize(draft.read_text()) if successful else "No current manuscript. Quality scores are missing, not zero.\n"
            (stage / (code + ".md")).write_text("# " + code + "\n\n" + case["question"] + "\n\n" + text)
            facts = []
            limitation = "No manuscript"
            if successful:
                extracted, execution = CodexEngine(reviewer_model, 240, "low").execute(
                    "evaluation-extraction", {"manuscript": text},
                    "Extract every empirically checkable assertion, including mixed/normative paragraphs, as an exact contiguous substring. Preserve negation, population and numbers. No grading, paraphrase or support verdict. Report omissions and uncertainty.", FACT_EXTRACTION)
                validate(extracted, FACT_EXTRACTION)
                facts = list(dict.fromkeys(extracted["facts"]))
                if any(f not in text for f in facts):
                    raise ResearchError("評価用の事実抽出が原稿に一致しません。")
                save(root / (code + "-extraction.json"), {"result": extracted, "execution": execution, "scope": "AI extraction, human must inspect omissions"})
                limitation = "; ".join(extracted["limitations"])
                if len(facts) > 20:
                    facts = random.Random(108).sample(facts, 20)
            samples.append({"sample_id": code, "manuscript_available": successful,
                            "scores": {k: None for k in RUBRIC}, "comments": "",
                            "facts": [{"id": f"Fact-{i+1:02d}", "text": f, "verdict": None, "source_locator": "", "reason": ""} for i, f in enumerate(facts)],
                            "extraction_limitations": limitation, "omitted_factual_assertions": "",
                            "source_access_problems": "", "reference_set_relevance": ""})
        save(stage / "scores.json", {"samples": samples})
        (stage / "README.md").write_text("# Human evaluation\n\nScore prior_work, argument, counterarguments, qualification, traceability on 1–5 (1 poor, 3 mixed, 5 strong). Explain judgments. Leave unavailable manuscripts null.\n\nFor each fact choose supported, partial, contradicted, undetermined, or not_factual; record a primary-source page/section and reason. Check negation, numbers, scope, causal inference and extraction omissions. AI extraction is not ground truth. Source inaccessibility means undetermined, not contradicted.\n\nMethod identity is withheld, but formatting/content can still reveal it; blinding is imperfect. Return scores.json. Source links are in the manuscripts.\n")
        save(root / "private-sample-map.json", mapping)
        stage.rename(target)
    except BaseException:
        import shutil
        shutil.rmtree(stage)
        raise
    return {"packet": str(target), "human_evaluation": "pending", "private_mapping": "Do not give private-sample-map.json to evaluator"}


def import_scores(output, path):
    root = Path(output).resolve()
    original = read(root / "blind-packet" / "scores.json")
    submitted = read(path)
    mapping = read(root / "private-sample-map.json")
    if not isinstance(submitted, dict) or not isinstance(submitted.get("samples"), list):
        raise ResearchError("評価票の形式が不正です。")
    samples = submitted["samples"]
    if any(not isinstance(s, dict) or not isinstance(s.get("scores"), dict) or not isinstance(s.get("facts"), list)
           or any(not isinstance(f, dict) or not all(isinstance(f.get(k), str) for k in ("id", "text", "source_locator", "reason")) for f in s["facts"]) for s in samples):
        raise ResearchError("評価票の採点・事実項目の形式が不正です。")
    if len(samples) != len(mapping) or {s.get("sample_id") for s in samples} != set(mapping):
        raise ResearchError("評価対象が欠けるか重複しています。")
    for sample in samples:
        previous = next(s for s in original["samples"] if s["sample_id"] == sample["sample_id"])
        if sample["manuscript_available"] != previous["manuscript_available"] or [(f["id"], f["text"]) for f in sample["facts"]] != [(f["id"], f["text"]) for f in previous["facts"]]:
            raise ResearchError("評価票の原稿・事実主張を書き換えられません。")
        if set(sample["scores"]) != set(RUBRIC):
            raise ResearchError("採点項目が一致しません。")
        for value in sample["scores"].values():
            if previous["manuscript_available"]:
                if type(value) is not int or not 1 <= value <= 5:
                    raise ResearchError("品質評価は1〜5の整数が必要です。")
            elif value is not None:
                raise ResearchError("原稿がない実行を品質採点できません。")
        for fact in sample["facts"]:
            if fact["verdict"] not in ("supported", "partial", "contradicted", "undetermined", "not_factual") or not fact["reason"].strip():
                raise ResearchError("各事実の判定と理由が必要です。")
            if fact["verdict"] in ("supported", "partial", "contradicted") and not fact["source_locator"].strip():
                raise ResearchError("支持・反証には原資料の箇所が必要です。")
    dest = root / ("human-scores-" + hashlib.sha256(Path(path).read_bytes()).hexdigest()[:12] + ".json")
    if dest.exists():
        raise ResearchError("この評価はすでに取り込み済みです。")
    save(dest, {"submitted_at": now(), "input_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(), "samples": samples})
    manifest = read(root / "manifest.json")
    manifest.update(human_evaluation="completed", human_scores=dest.name)
    save(root / "manifest.json", manifest)
    return report(root)


def identifiers(text):
    found = set()
    for value in re.findall(r"(?:arxiv\.org/(?:abs|pdf)/|arxiv:)([0-9]{4}\.[0-9]{4,5})(?:v[0-9]+)?", text, re.I):
        found.add("arxiv:" + value)
    for value in re.findall(r"10\.\d{4,9}/[^\s<>\]\)\"]+", text, re.I):
        value = value.rstrip(".,;:")
        arxiv = re.fullmatch(r"10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})(?:v[0-9]+)?", value, re.I)
        found.add("arxiv:" + arxiv[1] if arxiv else "doi:" + value.lower())
    return found


def metrics(root, run, case):
    path = root / run["path"]
    draft = path / "draft.md"
    final_ids = identifiers(draft.read_text()) if draft.exists() else set()
    expected = {r["id"] for r in case["references"]}
    result = {"expected_reference_count": len(expected), "expected_in_final_manuscript": sorted(expected & final_ids),
              "identifiable_final_citation_ids": sorted(final_ids),
              "scope": "Identifier matching only. Missing identifiers are not proof that a paper was unread; relevance and support require human evaluation."}
    database = path / "study.sqlite3"
    if database.exists():
        # Read-only; never migrate an evaluation DB or alter its historical status during report.
        import sqlite3
        conn = sqlite3.connect("file:" + str(database) + "?mode=ro", uri=True)
        try:
            observed = set()
            for row in conn.execute("SELECT doi,url,external_id FROM papers"):
                observed.update(identifiers(" ".join(str(x) for x in row)))
            result["expected_discovered_in_ledger"] = sorted(expected & observed)
            result["fetched_documents"] = conn.execute("SELECT COUNT(*) FROM documents WHERE status='extracted'").fetchone()[0]
            result["discovery_attempts"] = [dict(zip(("provider", "status", "count"), r)) for r in conn.execute("SELECT provider,status,COUNT(*) FROM searches GROUP BY provider,status")]
            latest = conn.execute("SELECT id FROM artifacts WHERE kind='verify' ORDER BY id DESC LIMIT 1").fetchone()
            result["native_fact_verdicts"] = dict(conn.execute("SELECT verdict,COUNT(*) FROM facts WHERE verification_id=? GROUP BY verdict", (latest[0],)).fetchall()) if latest else None
            result["native_verification_scope"] = "Harness AI judgments only, not an independently comparable quality score or human ground truth."
        finally:
            conn.close()
    return result


def report(output):
    root = Path(output).resolve()
    manifest = read(root / "manifest.json")
    observed_runs = [dict(run, metrics=metrics(root, run, next(c for c in manifest["cases"]["cases"] if c["id"] == run["case_id"]))) for run in manifest["runs"]]
    result = {"human_evaluation": manifest["human_evaluation"], "limitations": manifest["limitations"], "runs": observed_runs}
    if manifest.get("human_scores"):
        mapping = read(root / "private-sample-map.json")
        result["human_scores"] = [dict(s, **mapping[s["sample_id"]]) for s in read(root / manifest["human_scores"])["samples"]]
    lines = ["# Exploratory evaluation", "", "Human evaluation: " + manifest["human_evaluation"], "",
             "| Case | Method | Status | Seconds |", "| --- | --- | --- | --- |"]
    for run in manifest["runs"]:
        lines.append(f"| {run['case_id']} | {run['method']} | {run['status']} | {run.get('elapsed_seconds', 0):.1f} |")
    lines += ["", "## Limits", ""] + ["- " + x for x in manifest["limitations"]]
    lines += ["", "## Observations", "", "| Case | Method | Reference IDs in final / limited set | Native fact verification |", "| --- | --- | --- | --- |"]
    for run in observed_runs:
        m = run["metrics"]
        lines.append(f"| {run['case_id']} | {run['method']} | {len(m['expected_in_final_manuscript'])}/{m['expected_reference_count']} | {m.get('native_fact_verdicts', 'not instrumented')} |")
    lines += ["", "Identifier matches do not establish relevance or factual support. Native fact judgments are not a shared comparative quality metric."]
    if manifest["human_evaluation"] == "pending":
        lines += ["", "No human quality scores yet. No superiority conclusion is justified."]
    save(root / "report.json", result)
    (root / "report.md").write_text("\n".join(lines) + "\n")
    return {"report": str(root / "report.md"), "human_evaluation": result["human_evaluation"], "runs": len(result["runs"])}
