import datetime
import json
import math
import re
from collections import Counter

import anthropic

import db
import ledger
from credentials import get_secret
from llm import reply_from_response

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
MAX_EXTRACTION_ATTEMPTS = 3
MAX_CONSECUTIVE_API_ERRORS = 5

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


def _label(evidence):
    return evidence.get("evidence_id", "<no evidence_id>")


def request_extraction(client, evidence):
    """Make the extraction API call. Returns an LLMReply; raises ExtractionAPIError if the call itself fails."""
    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": build_extraction_prompt(evidence)}],
        )
    except Exception as e:
        raise ExtractionAPIError(f"evidence {_label(evidence)}: API call failed: {e}") from e
    return reply_from_response(response)


def parse_extraction_reply(reply, evidence_label):
    if reply.text is None:
        raise ExtractionResponseError(
            f"evidence {evidence_label}: response has no text block (stop_reason={reply.stop_reason!r})"
        )
    return parse_extraction_text(reply.text, evidence_label)


def parse_extraction_text(raw_text, evidence_label):
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ExtractionResponseError(f"evidence {evidence_label}: non-JSON response: {raw_text!r}") from e

    if not isinstance(parsed, dict):
        raise ExtractionResponseError(f"evidence {evidence_label}: expected a JSON object, got {raw_text!r}")

    if parsed.get("skip") is True:
        return None

    missing = {"signal_type", "summary", "confidence"} - parsed.keys()
    if missing:
        raise ExtractionResponseError(f"evidence {evidence_label}: response missing keys {missing}")

    if not isinstance(parsed["signal_type"], str) or parsed["signal_type"] not in SIGNAL_TYPES:
        raise ExtractionResponseError(f"evidence {evidence_label}: invalid signal_type {parsed['signal_type']!r}")

    summary = parsed["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise ExtractionResponseError(f"evidence {evidence_label}: invalid summary {summary!r}")

    confidence = parsed["confidence"]
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0):
        raise ExtractionResponseError(f"evidence {evidence_label}: invalid confidence {confidence!r}")

    entity = parsed.get("entity")
    return {
        "signal_type": parsed["signal_type"],
        "summary": summary,
        "confidence": float(confidence),
        "entity": entity if isinstance(entity, dict) else None,
        "effective_date": _iso_date_or_none(parsed.get("effective_date")),
    }


_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _iso_date_or_none(value):
    # Python 3.11's date.fromisoformat also accepts "20270531" and week dates like "2027-W22-1",
    # so require the exact YYYY-MM-DD shape first, then check that it is a real calendar date.
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        return None
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return None
    return value


def extract_topic(client, evidence: dict):
    return parse_extraction_reply(request_extraction(client, evidence), _label(evidence))


def extract_one(conn, client, evidence):
    """One evidence row through the effect sandwich: record intent (committed), call the model,
    then commit the outcome together with the ledger result. Returns the outcome name."""
    evidence_id = evidence["evidence_id"]
    call_id = ledger.start_call(
        conn, stage="extract", subject_key=evidence_id, model=EXTRACTION_MODEL,
        prompt_template=EXTRACTION_PROMPT_TEMPLATE,
    )

    try:
        reply = request_extraction(client, evidence)
    except ExtractionAPIError as e:
        ledger.finish_call(conn, call_id, status="api_error", error=str(e))
        db.record_extraction_failure(conn, evidence_id, call_id, count_attempt=False,
                                     max_attempts=MAX_EXTRACTION_ATTEMPTS)
        conn.commit()
        print(f"extraction API error, will retry next run: {e}")
        return "api_error"

    try:
        result = parse_extraction_reply(reply, evidence_id)
    except ExtractionResponseError as e:
        ledger.finish_call(conn, call_id, status="response_error", reply=reply, error=str(e))
        status = db.record_extraction_failure(conn, evidence_id, call_id, count_attempt=True,
                                              max_attempts=MAX_EXTRACTION_ATTEMPTS)
        conn.commit()
        print(f"unusable extraction response ({status}): {e}")
        return "response_error"

    ledger.finish_call(conn, call_id, status="succeeded", reply=reply,
                       parsed=result if result is not None else {"skip": True})
    if result is None:
        db.record_extraction_outcome(conn, evidence_id, "skipped", call_id, decided_by="extractor")
        outcome = "skipped"
    else:
        db.insert_signal_candidate(
            conn,
            evidence_id=evidence_id,
            signal_type=result["signal_type"],
            summary=result["summary"],
            confidence=result["confidence"],
            entity=result["entity"],
            effective_date=result["effective_date"],
            llm_call_id=call_id,
        )
        db.record_extraction_outcome(conn, evidence_id, "candidate", call_id, decided_by="extractor")
        outcome = "candidate"
    conn.commit()
    return outcome


def run(db_path="data/pi_agent.db", today=None, client=None):
    client = client or anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    conn = db.connect(db_path)
    db.init_db(conn)
    counts = Counter()
    try:
        abandoned = ledger.abandon_stale_calls(conn, "extract")
        if abandoned:
            print(f"marked {abandoned} interrupted extraction call(s) abandoned; their evidence will be retried")
        consecutive_api_errors = 0
        for evidence in db.get_evidence_pending_extraction(conn):
            outcome = extract_one(conn, client, evidence)
            counts[outcome] += 1
            consecutive_api_errors = consecutive_api_errors + 1 if outcome == "api_error" else 0
            if consecutive_api_errors >= MAX_CONSECUTIVE_API_ERRORS:
                raise ExtractionAPIError(
                    f"stopping after {consecutive_api_errors} consecutive API errors; rerun when the API is healthy"
                )
    finally:
        conn.close()
    print(("extraction: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))) if counts
          else "extraction: nothing pending")


if __name__ == "__main__":
    run()
