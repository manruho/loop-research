"""Developer-annotated classification regression, not independent research grading."""
import hashlib
import json
from pathlib import Path
from .common import ResearchError
from .engine import CodexEngine
from .schema import obj, array, string

SCHEMA = obj(classifications=array(obj(id=string(minimum=1), category=string(('uncertainty', 'normative', 'hypothesis', 'ledger', 'literature'))), minimum=1, maximum=100))
PROMPT = ('Classify each supplied statement, never evaluate its truth. Return one entry for every ID. '
          'Use ledger for concrete stored-activity claims (including unverifiable negative actions), '
          'literature for assertions about a cited study, normative for value proposals, hypothesis '
          'for conditional future tests, uncertainty for unestablished benefits/novelty or limits on '
          'what supplied passages can establish. An epistemic limitation does not imply a recorded '
          'action. Do not invent sources or metrics.')


def score(cases, result):
    expected = {c['id']: c['expected_class'] for c in cases}
    ids = [r['id'] for r in result['classifications']]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ResearchError('分類評価のIDに欠落・重複・未知の項目があります。')
    rows = [dict(r, expected=expected[r['id']], matches=r['category'] == expected[r['id']]) for r in result['classifications']]
    return {'matches': sum(r['matches'] for r in rows), 'total': len(rows), 'cases': rows,
            'notice': 'Developer annotations only; not independent human ground truth, extraction recall, or superiority evidence.'}


def run(output, model, cases_path='benchmarks/extraction-cases.json', timeout=300):
    root = Path(output)
    if root.exists():
        raise ResearchError('評価出力先が存在します。新しいディレクトリを指定してください。')
    content = Path(cases_path).read_bytes()
    cases = json.loads(content)['cases']
    root.mkdir(parents=True)
    meta = {'requested_model': model, 'cases_sha256': hashlib.sha256(content).hexdigest(), 'status': 'running'}
    def save(name, value):
        (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    save('manifest.json', meta)
    try:
        names = {f'case{i:03d}': c['id'] for i, c in enumerate(cases, 1)}
        result, execution = CodexEngine(model=model, timeout=timeout, reasoning_effort='low').execute(
            'extraction_classification', {'cases': [{'id': f'case{i:03d}', 'text': c['text']} for i, c in enumerate(cases, 1)]}, PROMPT, SCHEMA)
        save('model-result.json', result)
        save('execution.json', execution)
        returned = [r['id'] for r in result['classifications']]
        if len(returned) != len(set(returned)) or set(returned) != set(names):
            raise ResearchError('匿名化した評価IDに欠落・重複・未知の項目があります。')
        restored = {'classifications': [dict(r, id=names.get(r['id'], r['id'])) for r in result['classifications']]}
        report = score(cases, restored)
        save('report.json', report)
        meta['status'] = 'completed'
        return report
    except Exception as exc:
        meta.update(status='failed', error=str(exc))
        raise
    finally:
        save('manifest.json', meta)
