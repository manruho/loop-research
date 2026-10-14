"""Small, deterministic wall-time envelopes; never evidence or quality gates."""

POLICY = 'validation-reserve-v1'


def allocation(kind, total, remaining, timeout):
    # Up to 30% for verification and 15% for review. Unspent time is shared;
    # neither reservation promises that a model will finish within it.
    review = min(timeout, total * .15)
    verify = min(timeout, total * .30)
    reserve = 0 if kind == 'review' else review if kind == 'verify' else verify + review
    return {'policy': POLICY, 'total_seconds': total, 'remaining_seconds': max(0, remaining),
            'reserved_seconds': reserve,
            'timeout_seconds': max(0, min(timeout, remaining - reserve))}


COMPACT = {
    'orientation': 'Use short analytical explanations and 3-4 complementary queries, including counterevidence.',
    'exploration': 'Explore two genuinely competing positions. Use one concise sentence per scalar explanation and 1-2 items per notes list; preserve value premises, strongest objections, and bounded novelty.',
    'comparison': 'Assess EVERY supplied candidate, with one concise sentence per explanation and 1-2 unresolved items. Do not change the criteria for proceeding, holding or withdrawing.',
    'outline': 'Use three concise sections, each with 1-2 premises, the strongest relevant objection and limits. This is an argument plan; do not prewrite the manuscript or repeat the same argument in each field.',
    'draft': 'Override only the earlier length target: aim for a 600-900 word provisional working draft with all seven required section roles, usually one short block per section. Preserve fair prior work, value premises, strongest objections and limitations. Include only warranted factual assertions; expose evidence gaps rather than hiding them or claiming success.',
    'revise_draft': 'Keep the working draft concise while addressing EVERY supplied blocking issue. Retain all required section roles, counterarguments and qualifications.',
    'redesign': 'Use concise explanations and respond to EVERY supplied blocking issue. Preserve hold, withdrawal and further-research options.',
}


def prompt(kind, original):
    if kind not in COMPACT:
        return original
    return (original + '\nTIME-BOUNDED WORKING PROFILE: ' + COMPACT[kind]
            + ' Brevity never waives schema requirements, factual support, uncertainty, or human final judgment.')
