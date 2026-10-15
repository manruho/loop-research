"""Conservative scope-loss detection, not a general semantic entailment parser."""
import re

SCOPE_PROMPT = (
    "When splitting a conjunction, never detach a shared negation, exclusive quantifier, condition, "
    "modality, population or time restriction. Use overlapping contiguous spans that repeat the shared "
    "prefix, or retain the complete compound sentence when independent contiguous spans cannot preserve "
    "its meaning. For 'no tools, new searches, full-paper reading, or independent source verification', "
    "do not return 'new searches' or 'independent source verification' alone. Retain the whole negative "
    "conjunction if necessary and explain each unsupported component. Likewise retain 'only', 'unless', "
    "'if', 'may', and temporal restrictions with their assertions. Do not paraphrase to insert missing words. "
    "A compound span must account for every component; if eight checks are insufficient, mark coverage "
    "incomplete rather than truncating. Extract substantive claims comparing prior work with the proposal, "
    "including claims that cited works are substantial precedents, separately from individual paper summaries. "
)
# Deliberately bounded English cues for English manuscripts. No claim of full
# linguistic coverage: reviewer inspection of full context remains necessary.
CUES = re.compile(r"\b(?:no|not|never|without|neither|nor|only|unless|if|except|may|might|could|"
                  r"before|after|during|previously|formerly|currently)\b|\b\w+n['’]t\b", re.I)
BOUNDARY = re.compile(r'[.!?](?:[\"”’\)]*)\s+(?=[A-Z])|\n')


def scope_loss(context, start, end):
    """Return omitted cue offsets in enclosing sentences; never rewrite a claim."""
    left, right = 0, len(context)
    for match in BOUNDARY.finditer(context):
        if match.end() <= start:
            left = match.end()
        elif match.start() + 1 >= end:
            right = match.start() + 1
            break
    omitted = [{'text': m.group(), 'start': left + m.start(), 'end': left + m.end()}
               for m in CUES.finditer(context[left:right])
               if not (start <= left + m.start() and left + m.end() <= end)]
    return {'start': left, 'end': right, 'text': context[left:right], 'omitted_cues': omitted}
