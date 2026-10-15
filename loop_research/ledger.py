"""Host-owned record snapshots and deterministic checks of extracted process claims."""
import hashlib
import json
import re
from .common import ResearchError, label
from .schema import obj, array, string
from .assertion_spans import scope_loss

CHECK = obj(path=string(minimum=1, maximum=300), operator=string(('equals', 'not_equals', 'contains', 'all_equal')),
            expected_json=string(minimum=1, maximum=2000))
CLAIM = obj(id=string(minimum=1, maximum=30), block_id=string(minimum=1, maximum=30),
            field=string(('text', 'qualification')), text=string(minimum=1, maximum=1800),
            occurrence={'type': 'integer', 'minimum': 1, 'maximum': 1000},
            checks=array(CHECK, minimum=1, maximum=8), reason=string(minimum=1))


def reject_constant(value):
    raise ValueError("Nonfinite JSON constant: " + value)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def snapshot(store, scope, context, draft=None, discovery_deferred=None):
    searches = scope['searches']
    keyword = [s for s in searches if not s['query'].startswith('graph:')]
    readings = [r for r in scope['readings'] if r['research_question'] == store.study_state()['question']]
    selected = store.study_state()['selected_claim_id']
    claim = store.claim_row(selected) if selected else None
    evidence = list(store.db.execute("SELECT id,status,relation FROM evidence WHERE claim_id=? AND claim_revision=? ORDER BY id",
                                    (claim['id'], claim['revision']))) if claim else []
    documents = {d['id']: {'paper_id': p['id'], 'origin': d['origin'], 'sha256': d['sha256'], 'status': d['status']}
                 for p in store.list_papers() for d in p['documents']}
    paper_pages = {}
    for reading in readings:
        pid = documents[reading['document_id']]['paper_id']
        paper_pages.setdefault(pid, set()).add(reading['page'])
    records = {
        'search_count': len(searches), 'keyword_search_count': len(keyword),
        'completed_search_count': sum(s['status'] == 'completed' for s in searches),
        'search_providers': sorted({s['provider'] for s in searches}),
        'searches': {s['id']: {k: s[k] for k in ('provider', 'query', 'status')} for s in searches},
        'readings': {r['reading_id']: {k: r[k] for k in ('document_id', 'page', 'start', 'characters')} for r in readings},
        'evidence_status': {label(e['id'], 'E'): e['status'] for e in evidence},
        'evidence_relation': {label(e['id'], 'E'): e['relation'] for e in evidence},
        'paper_scope': {pid: p['seen_scope'] for pid, p in context['papers'].items()},
        'paper_titles': {pid: p['title'] for pid, p in context['papers'].items()},
        'citation_graph': scope['citation_graph'],
        'documents': documents,
        'paper_read_pages': {pid: sorted(pages) for pid, pages in paper_pages.items()},
    }
    deferred = draft['input'].get('discovery_deferred') if draft else discovery_deferred
    if type(deferred) is bool:
        records['discovery_deferred'] = deferred
    if draft and draft.get('task_id'):
        writer = store.show_task(draft['task_id'])
        writer_scope = writer['input']['data'].get('context', {}).get('scope', {})
        if isinstance(writer_scope.get('searches'), list):
            records['raw_search_responses_supplied'] = any(isinstance(q, dict) and q.get('response') is not None for q in writer_scope['searches'])
        observation = (writer.get('execution') or {}).get('tool_observation')
        if observation and not observation['unknown_item_types']:
            records['author_tool_call_count'] = observation['known_tool_items']
        records['author_web_search_config'] = writer['config'].get('web_search', 'unrecorded')
    return {'records': records, 'sha256': hashlib.sha256(canonical(records).encode()).hexdigest(),
            'scope': 'Searches are study-wide attempts (including failures and citation traversal); readings belong to the current question; evidence belongs to the selected current claim. Records attest stored activity, not external source truth or exhaustive research.'}


def resolve(records, path):
    if not path.startswith('/') or len(path.split('/')) > 8:
        raise KeyError(path)
    if path.startswith('/records/'):
        path = path[len('/records'):]
    current = records
    for token in path[1:].split('/'):
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(current, dict) and token in current:
            current = current[token]
        elif isinstance(current, list) and re.fullmatch(r'0|[1-9][0-9]*', token) and int(token) < len(current):
            current = current[int(token)]
        else:
            raise KeyError(path)
    return current


def normalize(data, claims, manuscript):
    from .verification import locate
    from .common import identifier, label
    result, ids = [], set()
    ledger = data['ledger_snapshot']
    if hashlib.sha256(canonical(ledger['records']).encode()).hexdigest() != ledger['sha256']:
        raise ResearchError('実行記録スナップショットのハッシュが一致しません。')
    for raw in claims:
        item = dict(raw)
        item['id'] = label(identifier(item['id'], 'G'), 'G')
        if item['id'] in ids or item['block_id'] not in manuscript:
            raise ResearchError('実行記録主張のIDまたは対象段落が不正です。')
        ids.add(item['id'])
        item['start'], item['end'] = locate(manuscript[item['block_id']][item['field']], item['text'], item['occurrence'])
        item['context_id'] = item['block_id'] + '.' + item['field']
        item['scope_check'] = scope_loss(manuscript[item['block_id']][item['field']], item['start'], item['end'])
        checks = []
        for raw_check in item['checks']:
            check = dict(raw_check)
            try:
                expected = json.loads(check['expected_json'], parse_constant=reject_constant)
                canonical(expected)
            except (ValueError, TypeError, RecursionError):
                check.update(verdict='undetermined', observed=None, reason='Malformed or nonfinite expected_json; no comparison performed')
                checks.append(check)
                continue
            try:
                observed = resolve(ledger['records'], check['path'])
            except KeyError:
                check.update(verdict='undetermined', observed=None, reason='Record path unavailable')
            else:
                op = check['operator']
                if op == 'equals':
                    matches = canonical(observed) == canonical(expected)
                elif op == 'not_equals' and type(observed) in (str, int, float, bool) and type(observed) is type(expected):
                    comparison = re.search(r'\b(?:differs?|different|unequal|not equal|does not match|do not match|not the same)\b|一致しない|異なる', item['text'], re.IGNORECASE)
                    if not comparison:
                        check.update(verdict='undetermined', observed=observed, reason='Negative comparison is not explicit in the extracted span; semantic mapping requires review')
                        checks.append(check)
                        continue
                    matches = observed != expected
                elif op == 'contains' and isinstance(observed, list):
                    matches = any(canonical(v) == canonical(expected) for v in observed)
                elif op == 'contains' and isinstance(observed, dict) and isinstance(expected, str):
                    matches = expected in observed
                elif op == 'all_equal' and isinstance(observed, (dict, list)) and observed:
                    matches = all(canonical(v) == canonical(expected) for v in (observed.values() if isinstance(observed, dict) else observed))
                else:
                    check.update(verdict='undetermined', observed=observed, reason='Unsupported operation or empty collection')
                    checks.append(check)
                    continue
                check.update(verdict='supported' if matches else 'contradicted', observed=observed, reason='Deterministic comparison with stored snapshot')
            if check['path'].removeprefix('/records').startswith('/author_web_search_config') and check['verdict'] == 'supported':
                action_claim = re.search(r'without\s+tools|no\s+tools|supplied\s+material\s+only|only\s+supplied\s+material|no\s+additional\s+searches', item['text'], re.IGNORECASE)
                configuration_claim = re.search(r'configur|disabled|enabled|permitted|allowed|設定|無効|有効', item['text'], re.IGNORECASE)
                if action_claim or not configuration_claim:
                    check.update(verdict='undetermined', reason='Web-search configuration cannot establish actual tool use, search execution or exclusive source use')
            if check['path'].removeprefix('/records').startswith('/evidence_status') and check['verdict'] == 'supported':
                absence = re.search(r'not\s+human[- ]verified|(?:no|never|without)\b.{0,50}\b(?:human|manual)\b|人間による確認.{0,12}(?:ない|無し)|人間.{0,12}未確認', item['text'], re.IGNORECASE)
                if absence:
                    check.update(verdict='undetermined', reason='Evidence status cannot establish absence of human verification activity')
            # Recorded observations cannot establish exclusive material use or an
            # unqualified absence of real-world activity, even with intact syntax.
            path = check['path'].removeprefix('/records')
            activity = re.search(r'\b(?:no|never|without)\b.{0,100}\b(?:tools?|searches|reading|verification)\b|\bonly supplied material\b', item['text'], re.I)
            recorded = re.search(r'\b(?:ledger|records?|recorded|observed)\b', item['text'], re.I)
            if check['verdict'] == 'supported' and activity and not recorded and path.startswith((
                    '/author_tool_call_count', '/raw_search_responses_supplied', '/paper_read_pages')):
                check.update(verdict='undetermined', reason='Stored observations do not establish exhaustive absence of activity or exclusive material use')
            checks.append(check)
        verdicts = {c['verdict'] for c in checks}
        if item['scope_check']['omitted_cues']:
            verdicts.add('undetermined')
            # Keep deterministic expression results separate from span validity.
            item['scope_warning'] = 'Enclosing sentence has scope cues outside extracted span; re-extract with negation and restrictions preserved.'
        item['extraction_reason'] = item['reason']
        item['reason'] = 'Host ledger checks: ' + '; '.join(c['path'] + ': ' + c['verdict'] for c in checks)
        if item['scope_check']['omitted_cues']:
            item['reason'] = 'Host scope gate: ' + item['scope_warning'] + ' ' + item['reason']
        verdict = ('undetermined' if item['scope_check']['omitted_cues'] else
                   'contradicted' if 'contradicted' in verdicts else
                   'undetermined' if 'undetermined' in verdicts else 'supported')
        item.update(checks=checks, verdict=verdict,
                    snapshot_sha256=ledger['sha256'], host_reason='Host checks extracted expression; its mapping to the manuscript still requires review.',
                    qualification='Stored records only; not independent confirmation of activity or literature truth.')
        result.append(item)
    return result
