"""Small, deterministic wall-time envelopes; never evidence or quality gates."""

POLICY = 'validation-reserve-v2'


def allocation(kind, total, remaining, timeout):
    # Up to 30% for verification and 15% for review. Unspent time is shared;
    # neither reservation promises that a model will finish within it.
    review = min(timeout, total * .15)
    verify = min(timeout, total * .30)
    reserve = 0 if kind == 'review' else review if kind == 'verify' else verify + review
    return {'policy': POLICY, 'total_seconds': total, 'remaining_seconds': max(0, remaining),
            'reserved_seconds': reserve,
            'timeout_seconds': max(0, min(timeout, remaining - reserve))}


WORKING_DRAFT_WORDS = (600, 900)
WORKING_DRAFT_LENGTH = f'{WORKING_DRAFT_WORDS[0]}-{WORKING_DRAFT_WORDS[1]}'


COMPACT = {
    'orientation': 'Aim for 120 words of analysis plus 3-4 complementary queries, including counterevidence.',
    'selection': 'Keep each reason to about 10 English words; judge EVERY supplied candidate. Preserve uncertain and counterevidence classifications.',
    'reading': 'Use brief analytical statements and notes, retaining exact ORIGINAL-language quotations, every material qualification and the strongest objection. Do not imply a complete reading or reduce uncertainty for brevity.',
    'exploration': 'Explore two genuinely competing positions, aiming for 250-350 words TOTAL. Use one short sentence per scalar explanation and 1-2 items per notes list; preserve value premises, strongest objections, and bounded novelty.',
    'comparison': 'Assess EVERY supplied candidate, aiming for 150-250 words TOTAL with short explanations and 1-2 unresolved items. Do not change the criteria for proceeding, holding or withdrawing.',
    'outline': 'Aim for 180-250 words TOTAL across three concise sections, each with 1-2 premises, the strongest relevant objection and limits. This is an argument plan; do not prewrite the manuscript or repeat the same argument in each field.',
    'draft': f'Override only the earlier length target: aim for a {WORKING_DRAFT_LENGTH} word provisional working draft with all seven required section roles, usually one short block per section. Preserve fair prior work, value premises, strongest objections and limitations. Include only warranted factual assertions; expose evidence gaps rather than hiding them or claiming success.',
    'revise_draft': 'Keep the working draft concise while addressing EVERY supplied blocking issue. Retain all required section roles, counterarguments and qualifications.',
    'redesign': 'Use concise explanations and respond to EVERY supplied blocking issue. Preserve hold, withdrawal and further-research options.',
}


def prompt(kind, original):
    if kind not in COMPACT:
        return original
    return (original + '\nTIME-BOUNDED WORKING PROFILE: Override the default analytical-language preference: write concise English analytical fields for this task. '
            + COMPACT[kind]
            + ' Brevity never waives schema requirements, factual support, uncertainty, or human final judgment.')
