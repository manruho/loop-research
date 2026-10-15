"""Offline paired coverage diagnostic: selected text is never substantive reading."""
import hashlib
import json
from pathlib import Path
from .common import ResearchError
from .selection import page_sample, snippet, terms


def measure(cases):
    rows = []
    for case in cases:
        pages = [{'page': i, 'text': text} for i, text in enumerate(case['pages'], 1)]
        observed = {'case_id': case['id'], 'targets': len(case['targets'])}
        for name, structured in [('edges', False), ('structured', True)]:
            chosen = page_sample(pages, case['page_limit'], terms(case['query']), structured)
            excerpts = {p['page']: snippet(p['text'], terms(case['query']), structured) for p in chosen}
            exposed = sum(t['quote'] in excerpts.get(t['page'], {}).get('snippet', '') for t in case['targets'])
            observed[name] = {'targets_exposed': exposed, 'candidate_pages': len(chosen),
                              'source_characters_sent': sum(sum(s['end'] - s['start'] for s in e['snippet_spans']) for e in excerpts.values()),
                              'snippet_characters_sent': sum(len(e['snippet']) for e in excerpts.values()),
                              'selected_pages': list(excerpts)}
        rows.append(observed)
    return {'cases': rows, 'totals': {name: {
                key: sum(row[name][key] for row in rows)
                for key in ('targets_exposed', 'candidate_pages', 'source_characters_sent', 'snippet_characters_sent')}
            for name in ('edges', 'structured')}, 'targets': sum(row['targets'] for row in rows),
            'notice': 'Synthetic developer-authored excerpt exposure only; not semantic relevance, actual reading, discovery recall, model latency or research quality.'}


def run(output, cases_path='benchmarks/snippet-cases.json'):
    root = Path(output)
    if root.exists():
        raise ResearchError('評価出力先は既存です。新しいディレクトリを指定してください。')
    content = Path(cases_path).read_bytes()
    cases = json.loads(content)['cases']
    ids = [case['id'] for case in cases]
    if len(ids) != len(set(ids)) or not cases:
        raise ResearchError('評価課題のIDが重複しているか、課題がありません。')
    for case in cases:
        if not case['pages'] or type(case['page_limit']) is not int or not 1 <= case['page_limit'] <= 60:
            raise ResearchError('評価ページと上限が不正です。')
        for target in case['targets']:
            if not 1 <= target['page'] <= len(case['pages']) or not target['quote'] or target['quote'] not in case['pages'][target['page'] - 1]:
                raise ResearchError('評価対象の抜粋が指定ページに一致しません。')
    report = measure(cases)
    root.mkdir(parents=True)
    (root/'cases.json').write_bytes(content)
    (root/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    (root/'manifest.json').write_text(json.dumps({
        'cases_sha256': hashlib.sha256(content).hexdigest(),
        'source_sha256': {p: hashlib.sha256((Path(__file__).parent/p).read_bytes()).hexdigest() for p in ('selection.py', 'snippet_eval.py')},
        'model_calls': 0, 'status': 'completed'}, indent=2)+'\n')
    return report
