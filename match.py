import json
from datetime import date

import anthropic

import db
from credentials import get_secret
from weekutil import iso_week_string

MATCH_MODEL = "claude-sonnet-5"

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


def _call_matcher(client, candidates, existing_topics):
    if not candidates:
        return []

    prompt = build_matching_prompt(candidates, existing_topics)
    try:
        response = client.messages.create(
            model=MATCH_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(response.content[0].text)
    except Exception as e:
        raise MatchError(f"matching call failed: {e}") from e


def apply_matches(conn, candidates, decisions, week):
    decisions_by_index = {d["index"]: d for d in decisions}

    for i, candidate in enumerate(candidates):
        decision = decisions_by_index.get(i, {})
        matched_topic_id = decision.get("matched_topic_id")

        if matched_topic_id:
            topic_id = matched_topic_id
            db.update_topic_last_seen(conn, topic_id, week)
        else:
            new_topic = decision["new_topic"]
            topic_id = db.insert_canonical_topic(
                conn,
                slug=new_topic["slug"],
                name=new_topic["name"],
                description=new_topic["description"],
                aliases=[],
                first_seen=week,
                last_seen=week,
            )

        db.set_candidate_topic(conn, candidate["candidate_id"], topic_id)
        db.increment_topic_weekly_mentions(conn, topic_id, week, amount=1)


def run(db_path="data/pi_agent.db", today=None, client=None):
    week = iso_week_string(today or date.today())
    conn = db.connect(db_path)
    db.init_db(conn)

    client = client or anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    try:
        candidates = db.get_candidates_without_topic(conn)
        existing_topics = db.get_canonical_topics(conn)
        decisions = _call_matcher(client, candidates, existing_topics)
        apply_matches(conn, candidates, decisions, week)
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()


if __name__ == "__main__":
    run()
