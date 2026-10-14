"""Frozen candidate-set selection comparisons; labels never enter model inputs."""
import hashlib
import time
from pathlib import Path
from . import selection
from .common import ResearchError
from .engine import CodexEngine
from .evaluation import read, save, selection_metrics


def lexical(case):
    import tempfile
    from .store import ResearchStore
    from .workflow import ResearchWorkflow
    with tempfile.TemporaryDirectory() as root:
        store = ResearchStore(Path(root) / 'study.sqlite3', initialize=True)
        try:
            store.init(case['question'])
            ids = {}
            for candidate in case['candidates']:
                store.add_paper({'canonical_key': 'fixture:' + candidate['id'], 'provider': 'selection-fixture',
                                 'external_id': candidate['id'], 'title': candidate['title'],
                                 'url': 'https://fixture.invalid/' + candidate['id'],
                                 'abstract': candidate.get('abstract', ''), 'authors': [], 'metadata': {}})
                ids[store.list_papers()[-1]['id']] = candidate['id']
            workflow = ResearchWorkflow(store)
            workflow.orientation = lambda: {'content': {'key_terms': case.get('terms', []), 'search_queries': []}}
            return [ids[p['id']] for p in workflow.lexical_papers()]
        finally:
            store.close()


def run(output, cases_path, model=None, ranking_path=None, k=2, timeout=180):
    if bool(model) == bool(ranking_path):
        raise ResearchError('モデル指定または保存ランキング指定のいずれか一方が必要です。')
    root = Path(output)
    if root.exists():
        raise ResearchError('評価出力先は既存です。上書きしません。')
    cases = read(cases_path)['cases']
    # Validate IDs/labels before spending inference time or creating outputs.
    for case in cases:
        ids = [c['id'] for c in case['candidates']]
        if not ids or len(ids) > 60 or len(ids) != len(set(ids)) or any(c['label'] not in ('core', 'counter', 'context', 'unrelated') for c in case['candidates']):
            raise ResearchError('評価候補のID・ラベル・件数が不正です。')
    selection_metrics(cases, {c['id']: [] for c in cases}, k)
    root.mkdir(parents=True)
    manifest = {'cases_sha256': hashlib.sha256(Path(cases_path).read_bytes()).hexdigest(),
                'model': model, 'timeout': timeout, 'k': k, 'status': 'running',
                'source_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')},
                'prompt_sha256': hashlib.sha256(selection.PROMPT.encode()).hexdigest()}
    save(root / 'cases.json', read(cases_path))
    save(root / 'manifest.json', manifest)
    ranking, observations = {}, []
    try:
        if ranking_path:
            ranking = read(ranking_path)
            manifest['ranking_sha256'] = hashlib.sha256(Path(ranking_path).read_bytes()).hexdigest()
        else:
            engine = CodexEngine(model, timeout, 'low')
            for case in cases:
                data = {'question': case['question'], 'candidates': [
                    {key: value for key, value in c.items() if key in ('id', 'title', 'abstract')} for c in case['candidates']],
                    'scope': 'Frozen selection-only benchmark, not evidence of scientific findings.'}
                start = time.monotonic()
                raw, execution = engine.execute('selection', data, selection.PROMPT, selection.SCHEMA)
                selection.validate(data, raw)
                ranking[case['id']] = selection.order(raw)
                observations.append({'case_id': case['id'], 'elapsed_seconds': time.monotonic() - start, 'execution': execution})
                save(root / (case['id'] + '-selection.json'), raw)
        start = time.monotonic()
        old = {c['id']: lexical(c) for c in cases}
        result = {'lexical': selection_metrics(cases, old, k), 'selected': selection_metrics(cases, ranking, k),
                  'lexical_elapsed_seconds': time.monotonic() - start, 'observations': observations}
        save(root / 'ranking.json', ranking)
        save(root / 'report.json', result)
        manifest['status'] = 'completed'
        return result
    except Exception as exc:
        manifest.update(status='failed', error=str(exc))
        raise
    finally:
        save(root / 'manifest.json', manifest)
