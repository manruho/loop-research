"""Bounded semantic selection; selection snippets are never recorded as readings."""
from .schema import obj, array, string
from .common import ResearchError

SCHEMA = obj(judgments=array(obj(candidate_id=string(minimum=1, maximum=30),
                                 relevance=string(('core', 'counter', 'context', 'uncertain', 'unrelated')),
                                 reason=string(minimum=1, maximum=160)), maximum=60), limitations=array(string(minimum=1), maximum=12))
PROMPT = (
    'Select material for the research question by conceptual relevance, not vocabulary overlap. '
    'Judge every supplied candidate once. core addresses the central question; counter provides a '
    'credible rival position, null result, limitation or challenge; context is adjacent background. '
    'Use review_followup reading purposes and unresolved issue context when supplied, prioritizing the requested gap and counterevidence. '
    'Use uncertain when metadata or selection snippets cannot establish relevance, not unrelated. '
    'Explicitly preserve foundational frameworks with different terminology and strong counterevidence. '
    'A title/abstract/snippet is not proof of substantive support or full-text inspection. '
    'Explain selection and limitations; do not infer study results or fabricate sources.'
    ' Keep each reason to one brief sentence (about 15 words or 60 Japanese characters); do not summarize the candidate.'
)


def data(store, guidance, document=None):
    state = store.study_state()
    if document:
        doc = store.document_row(document)
        pages = list(store.db.execute('SELECT page,text FROM pages WHERE document_id=? ORDER BY page', (doc['id'],)))
        # Uniform sampling prevents term frequency from permanently excluding pages.
        pages = [p for p in pages if p['text'].strip()]
        limit = state['settings']['selection_page_limit']
        if len(pages) > limit:
            pages = [pages[round(i * (len(pages) - 1) / (limit - 1))] for i in range(limit)] if limit > 1 else pages[:1]
        candidates = [{'id': f"{document}:{p['page']}", 'page': p['page'],
                       'snippet': p['text'][:600] + '\n[...]\n' + p['text'][-600:]} for p in pages]
        target = {'document_id': document, 'sha256': doc['sha256']}
    else:
        candidates = []
        for p in store.list_papers():
            row = store.paper_row(p['id'])
            candidates.append({'id': p['id'], 'title': row['title'], 'abstract': row['abstract'][:1200],
                               'provider': row['provider']})
        if len(candidates) > 60:
            manual = [p for p in candidates if p['provider'] == 'manual'][:20]
            rest = [p for p in candidates if p not in manual]
            slots = 60 - len(manual)
            candidates = manual + [rest[round(i * (len(rest) - 1) / (slots - 1))] for i in range(slots)]
        target = {'document_id': None}
    return {'question': state['question'], 'human_guidance': guidance, 'target': target,
            'candidates': candidates, 'selection_contract': 2,
            'scope': 'Metadata and bounded selection snippets only; not substantive reading or evidence acquisition.'}


def validate(data, result):
    ids = [r['candidate_id'] for r in result['judgments']]
    if len(ids) != len(set(ids)) or set(ids) != {c['id'] for c in data['candidates']}:
        raise ResearchError('選別の候補IDに欠落・重複・未知の項目があります。')


def order(result):
    groups = {k: [] for k in ('core', 'counter', 'context', 'uncertain')}
    for item in result['judgments']:
        if item['relevance'] in groups:
            groups[item['relevance']].append(item['candidate_id'])
    chosen = []
    for i in range(max(len(groups['core']), len(groups['counter']))):
        for kind in ('core', 'counter'):
            if i < len(groups[kind]):
                chosen.append(groups[kind][i])
    return chosen + groups['uncertain'] + groups['context']
