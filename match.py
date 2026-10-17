import json
import re
from dataclasses import dataclass, field
from datetime import date

import anthropic

import db
import ledger
from credentials import get_secret
from llm import reply_from_response
from weekutil import iso_week_string

MATCH_MODEL = "claude-sonnet-5"
MAX_MATCH_ATTEMPTS = 3
MATCH_RETRY_ROUNDS = 1

MATCHING_PROMPT_TEMPLATE = """You are matching newly observed product feedback signals against a \
canonical registry of existing topics for the same product.

Existing canonical topics:
{existing_topics_json}

New signal candidates observed this week:
{candidates_json}

For each candidate (by its "index"), decide whether it is semantically the same as one of the \
existing canonical topics, or represents a genuinely new topic.

Respond with ONLY a JSON array, one object per candidate, in the same order as given:
[
  {{"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": null}},
  {{"index": 1, "matched_topic_id": null, "new_topic": {{"name": "Export to CSV", "slug": "export-to-csv", "description": "User wants CSV export"}}}}
]

Use "matched_topic_id": null and fill in "new_topic" only when the candidate is a genuinely new topic, \
not just the same signal_type as an existing one. Slugs must be lowercase, hyphen-separated, and unique.
"""


class MatchError(Exception):
    pass


class MatchAPIError(MatchError):
    """The API call itself failed (rate limit, network, etc.) -- not a model mistake."""


class MatchResponseError(MatchError):
    """The model responded, but the response was unusable (no text block, or not valid JSON)."""


def build_matching_prompt(candidates, existing_topics):
    existing_summary = [
        {"topic_id": t["topic_id"], "name": t["name"], "description": t["description"]}
        for t in existing_topics
    ]
    candidate_summary = [
        {"index": i, "signal_type": c["signal_type"], "summary": c["summary"]}
        for i, c in enumerate(candidates)
    ]
    return MATCHING_PROMPT_TEMPLATE.format(
        existing_topics_json=json.dumps(existing_summary, indent=2),
        candidates_json=json.dumps(candidate_summary, indent=2),
    )


def request_matching(client, candidates, existing_topics):
    try:
        response = client.messages.create(
            model=MATCH_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": build_matching_prompt(candidates, existing_topics)}],
        )
    except Exception as e:
        raise MatchAPIError(f"matching call failed: {e}") from e
    return reply_from_response(response)


def parse_matching_reply(reply):
    if reply.text is None:
        raise MatchResponseError(f"matching response has no text block (stop_reason={reply.stop_reason!r})")
    try:
        return json.loads(reply.text)
    except json.JSONDecodeError as e:
        raise MatchResponseError(f"matching call returned non-JSON response: {reply.text!r}") from e


def call_matcher(client, candidates, existing_topics):
    if not candidates:
        return []
    return parse_matching_reply(request_matching(client, candidates, existing_topics))


_SLUG_JUNK_RE = re.compile(r"[^a-z0-9]+")


def normalize_slug(text):
    """Lowercase, hyphen-separated, [a-z0-9] only. Returns "" when nothing usable is left."""
    return _SLUG_JUNK_RE.sub("-", text.lower()).strip("-") if isinstance(text, str) else ""


@dataclass
class Resolution:
    """What to do with one candidate, decided from the matcher's output before touching the database."""
    index: int
    action: str  # "match" | "create" | "invalid"
    topic_id: str | None = None
    new_topic: dict | None = None
    reason: str | None = None


def resolve_decisions(candidates, decisions, existing_topics):
    """Turn raw matcher output into one Resolution per candidate. Pure and total: it never raises,
    whatever `decisions` contains, so no model output can crash the stage."""
    known_ids = {t["topic_id"] for t in existing_topics}
    id_by_slug = {t["slug"]: t["topic_id"] for t in existing_topics if t.get("slug")}

    if not isinstance(decisions, list):
        reason = f"matcher returned {type(decisions).__name__}, expected a list"
        return [Resolution(i, "invalid", reason=reason) for i in range(len(candidates))]

    by_index = {}
    for decision in decisions:
        if isinstance(decision, dict):
            index = decision.get("index")
            if isinstance(index, int) and not isinstance(index, bool):
                by_index.setdefault(index, []).append(decision)

    resolutions = []
    for i in range(len(candidates)):
        got = by_index.get(i, [])
        if len(got) != 1:
            resolutions.append(Resolution(i, "invalid", reason="no decision" if not got else f"{len(got)} decisions"))
        else:
            resolutions.append(_resolve_one(i, got[0], known_ids, id_by_slug))
    return resolutions


def _resolve_one(index, decision, known_ids, id_by_slug):
    topic_id = decision.get("matched_topic_id")
    new_topic = decision.get("new_topic")
    if topic_id:
        if new_topic:
            return Resolution(index, "invalid", reason="both matched_topic_id and new_topic are set")
        if not isinstance(topic_id, str) or topic_id not in known_ids:
            return Resolution(index, "invalid", reason=f"unknown topic id {topic_id!r}")
        return Resolution(index, "match", topic_id=topic_id)

    if not isinstance(new_topic, dict):
        return Resolution(index, "invalid", reason="neither matched_topic_id nor new_topic is set")
    name, description = new_topic.get("name"), new_topic.get("description")
    if not (isinstance(name, str) and name.strip() and isinstance(description, str) and description.strip()):
        return Resolution(index, "invalid", reason="new_topic needs a non-empty name and description")
    slug = normalize_slug(new_topic.get("slug")) or normalize_slug(name)
    if not slug:
        return Resolution(index, "invalid", reason="new_topic has no usable slug")
    if slug in id_by_slug:
        return Resolution(index, "match", topic_id=id_by_slug[slug], reason=f"new topic slug {slug!r} already exists")
    return Resolution(index, "create", new_topic={"name": name.strip(), "slug": slug, "description": description.strip()})


@dataclass
class ApplyOutcome:
    matched: int = 0
    created: list = field(default_factory=list)
    invalid: list = field(default_factory=list)


def apply_resolutions(conn, candidates, resolutions, week, match_call_id=None):
    """Write resolutions to the database. Does not commit. Invalid ones only count an attempt."""
    outcome = ApplyOutcome()
    created_by_slug = {}
    for candidate, resolution in zip(candidates, resolutions):
        candidate_id = candidate["candidate_id"]
        if resolution.action == "invalid":
            db.record_match_failure(conn, candidate_id, resolution.reason)
            outcome.invalid.append((candidate_id, resolution.reason))
            continue

        if resolution.action == "match":
            topic_id = resolution.topic_id
            db.update_topic_last_seen(conn, topic_id, week)
            outcome.matched += 1
        else:
            slug = resolution.new_topic["slug"]
            topic_id = created_by_slug.get(slug)
            if topic_id is not None:
                outcome.matched += 1
            else:
                existing = db.get_canonical_topic_by_slug(conn, slug)
                if existing is not None:
                    topic_id = existing["topic_id"]
                    db.update_topic_last_seen(conn, topic_id, week)
                    outcome.matched += 1
                else:
                    topic_id = db.insert_canonical_topic(
                        conn, slug=slug, name=resolution.new_topic["name"],
                        description=resolution.new_topic["description"], aliases=[],
                        first_seen=week, last_seen=week,
                    )
                    outcome.created.append(topic_id)
                created_by_slug[slug] = topic_id

        db.set_candidate_topic(conn, candidate_id, topic_id, match_call_id=match_call_id)
        db.increment_topic_weekly_mentions(conn, topic_id, week, amount=1)
    return outcome


def apply_matches(conn, candidates, decisions, week):
    existing = db.get_canonical_topics(conn)
    return apply_resolutions(conn, candidates, resolve_decisions(candidates, decisions, existing), week)


def match_batch(conn, client, candidates, week):
    """One matching call through the effect sandwich. The response is committed before it is
    applied, so a crash while applying loses no paid work: replay_unapplied() finishes the job."""
    existing = db.get_canonical_topics(conn)
    call_id = ledger.start_call(
        conn, stage="match", subject_key=f"{week}:{candidates[0]['candidate_id']}", model=MATCH_MODEL,
        prompt_template=MATCHING_PROMPT_TEMPLATE,
        request_meta={
            "week": week,
            "candidate_ids": [c["candidate_id"] for c in candidates],
            "topic_ids": [t["topic_id"] for t in existing],
        },
    )
    try:
        reply = request_matching(client, candidates, existing)
    except MatchAPIError as e:
        ledger.finish_call(conn, call_id, status="api_error", error=str(e))
        conn.commit()
        raise
    try:
        decisions = parse_matching_reply(reply)
    except MatchResponseError as e:
        ledger.finish_call(conn, call_id, status="response_error", reply=reply, error=str(e))
        for candidate in candidates:
            db.record_match_failure(conn, candidate["candidate_id"], str(e))
        conn.commit()
        raise

    ledger.finish_call(conn, call_id, status="succeeded", reply=reply, parsed=decisions)
    conn.commit()
    return _apply_call(conn, call_id, candidates, decisions, week, existing)


def _apply_call(conn, call_id, candidates, decisions, week, existing):
    resolutions = resolve_decisions(candidates, decisions, existing)
    outcome = apply_resolutions(conn, candidates, resolutions, week, match_call_id=call_id)
    ledger.mark_applied(conn, call_id)
    conn.commit()
    return outcome


def replay_unapplied(conn):
    """Apply matching responses that were paid for and stored but never applied (a crash between
    the two commits). Uses the week recorded with the call. Returns how many were replayed."""
    replayed = 0
    for call in ledger.get_unapplied_calls(conn, "match"):
        meta = call["request_meta"] or {}
        candidates = db.get_candidates_by_ids(conn, meta.get("candidate_ids", []))
        if len(candidates) != len(meta.get("candidate_ids", [])) or any(c["topic_id"] for c in candidates):
            print(f"match call {call['call_id']}: candidates changed since the call; not replaying it")
            ledger.mark_applied(conn, call["call_id"])
            conn.commit()
            continue
        _apply_call(conn, call["call_id"], candidates, call["parsed"], meta["week"], db.get_canonical_topics(conn))
        replayed += 1
    return replayed


def run(db_path="data/pi_agent.db", today=None, client=None):
    week = iso_week_string(today or date.today())
    conn = db.connect(db_path)
    db.init_db(conn)

    client = client or anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    try:
        ledger.abandon_stale_calls(conn, "match")
        replayed = replay_unapplied(conn)
        if replayed:
            print(f"matching: applied {replayed} stored response(s) from an interrupted run")
        for _ in range(1 + MATCH_RETRY_ROUNDS):
            candidates = db.get_candidates_without_topic(conn, max_attempts=MAX_MATCH_ATTEMPTS)
            if not candidates:
                break
            outcome = match_batch(conn, client, candidates, week)
            print(f"matching: {outcome.matched} matched, {len(outcome.created)} new topics, "
                  f"{len(outcome.invalid)} invalid")
            if not outcome.invalid:
                break
    finally:
        conn.close()


if __name__ == "__main__":
    run()
