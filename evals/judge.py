"""LLM-as-judge for the extraction eval's free-text summary field."""
import json
import re
from dataclasses import dataclass

JUDGE_MODEL = "claude-opus-5"
JUDGE_MAX_TOKENS = 16000

JUDGE_PROMPT_TEMPLATE = """You are grading one field of an automated product-feedback extractor.

The extractor read a Reddit post and wrote a one-line summary of what the post is saying. A human \
wrote a reference summary for the same post. Decide whether the extractor's summary is acceptable.

Original post title: {title}
Original post body: {content}

Reference summary (human-written): {reference_summary}

Extractor's summary: {actual_summary}

The extractor's summary PASSES if all of these hold:
1. Faithful: every claim in it is supported by the post. It adds no details, causes, numbers, \
products, or sentiment that the post does not state.
2. On-topic: it describes the same core issue or request as the reference summary. Different \
wording, more or less detail, or a different sentence structure is fine.
3. Useful: a product manager reading only this summary would understand what the user is saying.

It FAILS if it invents details, misrepresents what the user said, focuses on a side point \
instead of the main point, or is too vague to act on.

Respond with ONLY a JSON object with these exact keys:
- "verdict": "pass" or "fail"
- "reason": one sentence explaining the verdict
"""

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class JudgeError(Exception):
    pass


class JudgeAPIError(JudgeError):
    """The judge API call itself failed -- the example is errored, not graded."""


class JudgeResponseError(JudgeError):
    """The judge responded, but its verdict could not be parsed."""


@dataclass
class JudgeVerdict:
    passed: bool
    reason: str


def build_judge_prompt(evidence, reference_summary, actual_summary):
    return JUDGE_PROMPT_TEMPLATE.format(
        title=evidence["title"], content=evidence["content"],
        reference_summary=reference_summary, actual_summary=actual_summary,
    )


def judge_summary(evidence, reference_summary, actual_summary, client):
    prompt = build_judge_prompt(evidence, reference_summary, actual_summary)
    try:
        response = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=JUDGE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise JudgeAPIError(f"judge call failed: {e}") from e

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason in ("refusal", "max_tokens"):
        raise JudgeResponseError(f"judge stopped with stop_reason={stop_reason!r}")

    # Opus 5 runs adaptive thinking by default, so a thinking block may precede the answer.
    raw_text = next((block.text for block in response.content if block.type == "text"), None)
    if raw_text is None:
        raise JudgeResponseError("judge response has no text block")
    return parse_judge_response(raw_text)


def parse_judge_response(raw_text):
    text = raw_text.strip()
    fenced = _CODE_FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise JudgeResponseError(f"judge returned non-JSON response: {raw_text!r}") from e
    if not isinstance(parsed, dict):
        raise JudgeResponseError(f"judge returned {type(parsed).__name__}, expected an object: {raw_text!r}")

    verdict = parsed.get("verdict")
    normalized = verdict.strip().lower() if isinstance(verdict, str) else None
    if normalized not in ("pass", "fail"):
        raise JudgeResponseError(f"judge verdict must be 'pass' or 'fail', got {verdict!r}")

    reason = parsed.get("reason")
    return JudgeVerdict(passed=normalized == "pass", reason=reason.strip() if isinstance(reason, str) else "")
