"""Fixed diagnostic cases for extraction coverage and semantic ledger support."""
import hashlib
import json
from pathlib import Path
from .common import ResearchError
from .engine import CodexEngine
from .schema import obj, array, string
from .assertion_spans import SCOPE_PROMPT

SCHEMA = obj(cases=array(obj(id=string(minimum=1), assertions=array(obj(
    text=string(minimum=1), kind=string(('literature', 'comparison', 'ledger')),
    verdict=string(('supported', 'partial', 'undetermined')), reason=string(minimum=1)), maximum=100)), maximum=100))
PROMPT = SCOPE_PROMPT + ('Extract all checkable assertions from each text, including substantive comparisons and '
          'negative activity assertions. Return exact contiguous manuscript spans, one assertion per entry; '
          'preserve negation and scope. Classify as literature, comparison, or ledger. '
          'Assess whether supplied context supports the full meaning: supported, partial, or undetermined. '
          'Do not equate a passing record expression with semantic support. Do not infer unrecorded '
          'activity was absent. Do not use tools or outside sources. Explain each judgment.')
NOTICE = ('Developer annotations on selected diagnostic excerpts and paired controls; '
          'not independent human ground truth, full-manuscript recall, or superiority evidence.')


def span_key(text):
    """Ignore only a terminal period; retain all words, scope, and negation."""
    return text.removesuffix(".")


def score(cases, result):
    expected = {c['id']: c for c in cases}
    rows = result['cases']
    ids = [r['id'] for r in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(expected):
        raise ResearchError('評価IDに欠落・重複・未知の項目があります。')
    reports = []
    for row in rows:
        case = expected[row['id']]
        spans = set()
        for a in row['assertions']:
            if (a['text'] not in case['text'] or a['text'] in spans
                    or a['kind'] not in ('literature', 'comparison', 'ledger')
                    or a['verdict'] not in ('supported', 'partial', 'undetermined')
                    or not a['text'] or not a['reason'].strip()):
                raise ResearchError('抽出原文・分類・判定・理由が不正です。')
            spans.add(a['text'])
        checks = []
        for target in case['expected']:
            # Word-exact spans prevent paragraphs from masking individual omissions.
            matches = [a for a in row['assertions'] if span_key(a['text']) in {span_key(t) for t in [target['text']] + target.get('accepted_spans', [])}]
            checks.append(dict(target, extracted=bool(matches), correct=any(
                a['kind'] == target['kind'] and a['verdict'] == target['verdict'] for a in matches)))
        reports.append({'id': row['id'], 'checks': checks,
                        'unscored_assertions': [a for a in row['assertions'] if span_key(a['text']) not in {span_key(text) for t in case['expected'] for text in [t['text']] + t.get('accepted_spans', [])}]})
    checks = [c for r in reports for c in r['checks']]
    return {'required': len(checks), 'extracted': sum(c['extracted'] for c in checks),
            'correct': sum(c['correct'] for c in checks), 'cases': reports, 'notice': NOTICE}


def run(output, model=None, cases_path='benchmarks/verification-cases.json', timeout=300, result_path=None):
    if bool(model) == bool(result_path):
        raise ResearchError('--model または --result の一方だけを指定してください。')
    root = Path(output)
    if root.exists():
        raise ResearchError('評価出力先が存在します。')
    content = Path(cases_path).read_bytes()
    cases = json.loads(content)['cases']
    root.mkdir(parents=True)
    def save(name, value):
        (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    payload = {'cases': [dict(id=f'case{i:03d}', text=c['text'], context=c['context'])
                         for i, c in enumerate(cases, 1)]}
    names = {p['id']: c['id'] for p, c in zip(payload['cases'], cases)}
    meta = {'requested_model': model, 'cases_sha256': hashlib.sha256(content).hexdigest(),
            'evaluator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'prompt_sha256': hashlib.sha256(PROMPT.encode()).hexdigest(),
            'status': 'running', 'mode': 'saved-result' if result_path else 'model', 'notice': NOTICE}
    save('cases.json', json.loads(content))
    save('input.json', payload)
    try:
        if result_path:
            raw = Path(result_path).read_bytes()
            meta['result_sha256'] = hashlib.sha256(raw).hexdigest()
            result = json.loads(raw)
        else:
            result, execution = CodexEngine(model=model, timeout=timeout, reasoning_effort='low').execute(
                'verification_regression', payload, PROMPT, SCHEMA)
            save('execution.json', execution)
        save('model-result.json', result)
        if any(r['id'] not in names for r in result['cases']):
            raise ResearchError('未知の匿名評価IDです。')
        restored = {'cases': [dict(r, id=names[r['id']]) for r in result['cases']]}
        report = score(cases, restored)
        save('report.json', report)
        meta['status'] = 'completed'
        return report
    except Exception as exc:
        meta.update(status='failed', error=str(exc))
        raise
    finally:
        save('manifest.json', meta)
