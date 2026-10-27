"""Cheap pre-filter in front of the extractor. It decides only whether a post is worth extracting.

Triage never classifies and never writes a candidate; it can only skip. A skip is trusted when
the model says "skip" with confidence >= TRIAGE_SKIP_THRESHOLD. Anything else, including any
failure, sends the post to the extractor (fail open). See ADR 007.
"""
import json
import math
from dataclasses import dataclass

from llm import reply_from_response

TRIAGE_MODEL = "claude-haiku-5-5"
TRIAGE_EFFORT = "low"  # Haiku 5.5 defaults to medium; triage is a quick classification
TRIAGE_MAX_TOKENS = 2048
TRIAGE_SKIP_THRESHOLD = 0.9
TRIAGE_ROUTES = ("skip", "extract")

TRIAGE_PROMPT_TEMPLATE = """You are screening Reddit posts before a more expensive product-feedback extractor reads them.

Post title: {title}
Post body: {content}

Decide whether this post could contain product feedback about a software product: a complaint, \
a feature request, a usability or reliability problem, a pricing concern, a comparison with a \
competitor, an intent to switch, or a description of how someone uses the product.

Respond with ONLY a JSON object with these exact keys:
- "route": "skip" if the post is clearly not product feedback (spam, self-promotion, off-topic, \
empty, or AI-generated filler), otherwise "extract"
- "confidence": a number between 0.0 and 1.0 for how sure you are of the route
- "reason": a short phrase

When in doubt, choose "extract". Skipping real feedback is much worse than extracting noise.
"""


class TriageError(Exception):
    pass


class TriageAPIError(TriageError):
    """The API call itself failed."""


class TriageResponseError(TriageError):
    """The model responded, but the response was unusable."""


@dataclass(frozen=True)
class TriageDecision:
    route: str
    confidence: float
    reason: str


def build_triage_prompt(evidence):
    return TRIAGE_PROMPT_TEMPLATE.format(title=evidence["title"], content=evidence["content"])


def request_triage(client, evidence):
    try:
        response = client.messages.create(
            model=TRIAGE_MODEL,
            max_tokens=TRIAGE_MAX_TOKENS,
            output_config={"effort": TRIAGE_EFFORT},
            messages=[{"role": "user", "content": build_triage_prompt(evidence)}],
        )
    except Exception as e:
        raise TriageAPIError(f"evidence {evidence.get('evidence_id', '<no evidence_id>')}: triage call failed: {e}") from e
    return reply_from_response(response)


def parse_triage_reply(reply, evidence_label):
    if reply.text is None:
        raise TriageResponseError(f"evidence {evidence_label}: triage response has no text block "
                                  f"(stop_reason={reply.stop_reason!r})")
    try:
        parsed = json.loads(reply.text)
    except json.JSONDecodeError as e:
        raise TriageResponseError(f"evidence {evidence_label}: non-JSON triage response: {reply.text!r}") from e
    if not isinstance(parsed, dict):
        raise TriageResponseError(f"evidence {evidence_label}: expected a JSON object, got {reply.text!r}")

    route = parsed.get("route")
    if route not in TRIAGE_ROUTES:
        raise TriageResponseError(f"evidence {evidence_label}: invalid route {route!r}")
    confidence = parsed.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0):
        raise TriageResponseError(f"evidence {evidence_label}: invalid confidence {confidence!r}")
    reason = parsed.get("reason")
    return TriageDecision(route, float(confidence), reason.strip() if isinstance(reason, str) else "")


def triage(client, evidence):
    return parse_triage_reply(request_triage(client, evidence), evidence.get("evidence_id", "<no evidence_id>"))


def should_skip(decision, threshold=TRIAGE_SKIP_THRESHOLD):
    return decision.route == "skip" and decision.confidence >= threshold
