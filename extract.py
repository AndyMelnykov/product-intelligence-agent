import json

import anthropic

import db
from credentials import get_secret

SIGNAL_TYPES = {
    "complaint_rising",
    "complaint_falling",
    "new_feature_demand",
    "new_use_case",
    "usability_issue",
    "reliability_issue",
    "pricing_complaint",
    "switching_intent",
    "competitor_mention_rising",
    "customer_migration_intent",
    "churn_related_issue",
    "positive_adoption_pattern",
}

EXTRACTION_MODEL = "claude-sonnet-5"

EXTRACTION_PROMPT_TEMPLATE = """You are analyzing a Reddit post about a software product for product \
feedback signal extraction.

Post title: {title}
Post body: {content}

Respond with ONLY a JSON object with these exact keys:
- "signal_type": one of {signal_types}
- "summary": a one-line description of what's being said
- "confidence": a number between 0.0 and 1.0 for how confident you are in this classification
- "entity": an object {{"type": ..., "company": ..., "product": ...}} identifying the specific \
company or product this signal is about, or null if the post does not name one
- "effective_date": an ISO date ("YYYY-MM-DD") if the post states a specific effective date for a \
change (e.g. an end-of-support date), or null otherwise

If the post is not meaningful product feedback (spam, off-topic, low-effort, or clearly \
AI-generated filler), respond with exactly: {{"skip": true}}
"""


class ExtractionError(Exception):
    pass


class ExtractionAPIError(ExtractionError):
    """The API call itself failed (rate limit, network, etc.) -- not a model mistake."""


class ExtractionResponseError(ExtractionError):
    """The model responded, but the response was unusable (non-JSON, missing keys, bad signal_type)."""


def build_extraction_prompt(evidence: dict) -> str:
    return EXTRACTION_PROMPT_TEMPLATE.format(
        title=evidence["title"], content=evidence["content"],
        signal_types=", ".join(f'"{t}"' for t in sorted(SIGNAL_TYPES)),
    )


def extract_topic(client, evidence: dict):
    prompt = build_extraction_prompt(evidence)
    evidence_label = evidence.get("evidence_id", "<no evidence_id>")

    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise ExtractionAPIError(f"evidence {evidence_label}: API call failed: {e}") from e

    # Sonnet 5 runs adaptive thinking by default, so a thinking block may precede the answer.
    raw_text = next((block.text for block in response.content if block.type == "text"), None)
    if raw_text is None:
        raise ExtractionResponseError(
            f"evidence {evidence_label}: response has no text block "
            f"(stop_reason={getattr(response, 'stop_reason', None)!r})"
        )

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ExtractionResponseError(f"evidence {evidence_label}: non-JSON response: {raw_text!r}") from e

    if parsed.get("skip"):
        return None

    missing = {"signal_type", "summary", "confidence"} - parsed.keys()
    if missing:
        raise ExtractionResponseError(f"evidence {evidence_label}: response missing keys {missing}")

    if parsed["signal_type"] not in SIGNAL_TYPES:
        raise ExtractionResponseError(f"evidence {evidence_label}: invalid signal_type {parsed['signal_type']!r}")

    entity = parsed.get("entity")
    return {
        "signal_type": parsed["signal_type"],
        "summary": parsed["summary"],
        "confidence": parsed["confidence"],
        "entity": entity if isinstance(entity, dict) else None,
        "effective_date": parsed.get("effective_date"),
    }


def run(db_path="data/pi_agent.db", today=None, client=None):
    client = client or anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    conn = db.connect(db_path)
    db.init_db(conn)

    pending = db.get_evidence_without_candidate(conn)
    try:
        for evidence in pending:
            try:
                result = extract_topic(client, evidence)
            except ExtractionError as e:
                print(f"skipping evidence due to extraction error: {e}")
                continue
            if result is not None:
                db.insert_signal_candidate(
                    conn,
                    evidence_id=evidence["evidence_id"],
                    signal_type=result["signal_type"],
                    summary=result["summary"],
                    confidence=result["confidence"],
                    entity=result["entity"],
                    effective_date=result["effective_date"],
                )
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()


if __name__ == "__main__":
    run()
