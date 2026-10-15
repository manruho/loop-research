"""Bounded semantic selection; selection snippets are never recorded as readings."""
import json
import re
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


LIMITATIONS = re.compile(r'\b(?:limitations?|caveats?|counterexamples?|null results?|no significant|failed to|threats to validity)\b', re.I)
STOP = {'should', 'would', 'could', 'about', 'which', 'their', 'there', 'these', 'those', 'with', 'from', 'that', 'this', 'have', 'been', 'research'}


def terms(text):
    return {word.lower() for word in re.findall(r'[A-Za-z]{4,}', text)} - STOP


def priority(text, query_terms):
    # Capped distinct terms avoid a repetition-heavy page dominating selection.
    return len(terms(text) & query_terms) + 3 * bool(LIMITATIONS.search(text))


def uniform_indices(count, limit):
    if count <= limit:
        return list(range(count))
    return [round(i * (count - 1) / (limit - 1)) for i in range(limit)] if limit > 1 else [0]


def page_sample(pages, limit, query_terms, structured=True):
    if len(pages) <= limit or not structured:
        return [pages[i] for i in uniform_indices(len(pages), limit)]
    # Keep endpoints and uniform coverage, then spend remaining slots on local
    # relevance/limitations. This is a heuristic, not semantic truth or reading.
    indices = set(uniform_indices(len(pages), min(limit, max(2, (limit + 1) // 2))))
    ranked = sorted(range(len(pages)), key=lambda i: (-priority(pages[i]['text'], query_terms), i))
    for i in ranked:
        if len(indices) >= limit:
            break
        indices.add(i)
    return [pages[i] for i in sorted(indices)]


def snippet(text, query_terms, structured=True):
    if not structured:
        # Exact historical behavior for ablations, including short-page duplication.
        return {'snippet': text[:600] + '\n[...]\n' + text[-600:],
                'snippet_spans': [{'start': 0, 'end': min(600, len(text))},
                                  {'start': max(0, len(text) - 600), 'end': len(text)}]}
    if len(text) <= 1200:
        ranges = [(0, len(text))]
    else:
        # Preserve beginning/end and expose one 600-character interior window.
        # Candidate anchors include headings/paragraphs and query/limitation hits.
        anchors = {len(text) // 2}
        # At most one query hit and one limitation hit per spatial bucket:
        # repetition-heavy pages cannot create an unbounded scoring workload.
        buckets = {}
        for match in re.finditer(r"[A-Za-z]{4,}", text):
            if match.group().lower() in query_terms:
                buckets.setdefault(match.start() * 64 // len(text), match.start())
        anchors.update(buckets.values())
        buckets = {}
        for match in LIMITATIONS.finditer(text):
            buckets.setdefault(match.start() * 64 // len(text), match.start())
        anchors.update(buckets.values())
        candidates = []
        for anchor in sorted(anchors):
            start = max(300, min(len(text) - 900, anchor - 150))
            end = start + 600
            candidates.append((priority(text[start:end], query_terms), start, end))
        _, start, end = max(candidates, key=lambda item: (item[0], -abs((item[1] + 300) - len(text) // 2), -item[1]))
        ranges = [(0, 300), (start, end), (len(text) - 300, len(text))]
    return {'snippet': '\n[...]\n'.join(text[a:b] for a, b in ranges),
            'snippet_spans': [{'start': a, 'end': b} for a, b in ranges]}


def data(store, guidance, document=None, focus=None):
    state = store.study_state()
    if document:
        doc = store.document_row(document)
        pages = list(store.db.execute('SELECT page,text FROM pages WHERE document_id=? ORDER BY page', (doc['id'],)))
        pages = [p for p in pages if p['text'].strip()]
        limit = state['settings']['selection_page_limit']
        page_count = len(pages)
        structured = bool(state['settings'].get('structured_snippets', 1))
        query_terms = terms(state['question'] + ' ' + json.dumps([guidance, focus], ensure_ascii=False))
        pages = page_sample(pages, limit, query_terms, structured)
        candidates = [dict(id=f"{document}:{p['page']}", page=p['page'],
                           **snippet(p['text'], query_terms, structured)) for p in pages]
        target = {'document_id': document, 'sha256': doc['sha256'], 'nonempty_pages': page_count,
                  'omitted_pages': page_count - len(pages), 'structured_snippets': structured}
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
            'candidates': candidates, 'selection_contract': 3,
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
