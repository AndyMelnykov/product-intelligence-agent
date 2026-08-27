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

If the post is not meaningful product feedback (spam, off-topic, low-effort, or clearly \
AI-generated filler), respond with exactly: {{"skip": true}}
"""


class ExtractionError(Exception):
    pass


def build_extraction_prompt(evidence: dict) -> str:
    return EXTRACTION_PROMPT_TEMPLATE.format(
        title=evidence["title"], content=evidence["content"],
        signal_types=", ".join(f'"{t}"' for t in sorted(SIGNAL_TYPES)),
    )


def extract_topic(client, evidence: dict):
    prompt = build_extraction_prompt(evidence)

    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
    except Exception as e:
        raise ExtractionError(f"evidence {evidence['evidence_id']}: API call failed: {e}") from e

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"evidence {evidence['evidence_id']}: non-JSON response: {raw_text!r}") from e

    if parsed.get("skip"):
        return None

    missing = {"signal_type", "summary", "confidence"} - parsed.keys()
    if missing:
        raise ExtractionError(f"evidence {evidence['evidence_id']}: response missing keys {missing}")

    if parsed["signal_type"] not in SIGNAL_TYPES:
        raise ExtractionError(f"evidence {evidence['evidence_id']}: invalid signal_type {parsed['signal_type']!r}")

    return {"signal_type": parsed["signal_type"], "summary": parsed["summary"], "confidence": parsed["confidence"]}


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
                )
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()


if __name__ == "__main__":
    run()
