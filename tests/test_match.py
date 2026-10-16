import json

import pytest

import db
import match


class FakeContentBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResponse:
    def __init__(self, text):
        self.content = [FakeContentBlock(text)]


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(result)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


class FakeThinkingBlock:
    type = "thinking"
    thinking = ""


EXISTING_TOPICS = [
    {"topic_id": "TOPIC-0001", "name": "Dark mode support", "description": "Users requesting a dark theme"},
]
CANDIDATES = [{"signal_type": "new_feature_demand", "summary": "Another dark mode request"}]


def seed_db(tmp_path):
    db_path = tmp_path / "pi_agent.db"
    conn = db.connect(str(db_path))
    db.init_db(conn)
    return conn


def seed_existing_topic(conn):
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support",
        description="Users requesting a dark theme option", aliases=[],
        first_seen="2026-W30", last_seen="2026-W31",
    )
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=2)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W31", amount=3)
    conn.commit()
    return topic_id


def seed_candidate(conn, summary, signal_type="new_feature_demand"):
    evidence_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="/x",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="t", content="c", metadata={},
    )
    candidate_id = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type=signal_type, summary=summary, confidence=0.9,
    )
    conn.commit()
    return candidate_id


def test_apply_matches_increments_existing_topic_on_match(tmp_path):
    conn = seed_db(tmp_path)
    topic_id = seed_existing_topic(conn)
    candidate_id = seed_candidate(conn, "Another dark mode request")

    candidates = db.get_candidates_without_topic(conn)
    decisions = [{"index": 0, "matched_topic_id": topic_id, "new_topic": None}]

    match.apply_matches(conn, candidates, decisions, week="2026-W33")
    conn.commit()

    assert db.get_topic_weekly_mentions(conn, topic_id)["2026-W33"] == 1
    updated_candidates = conn.execute(
        "SELECT topic_id FROM signal_candidate WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    assert updated_candidates["topic_id"] == topic_id


def test_apply_matches_creates_new_topic_on_no_match(tmp_path):
    conn = seed_db(tmp_path)
    seed_candidate(conn, "User wants CSV export")

    candidates = db.get_candidates_without_topic(conn)
    decisions = [{
        "index": 0, "matched_topic_id": None,
        "new_topic": {"name": "Export to CSV", "slug": "export-to-csv", "description": "User wants CSV export"},
    }]

    match.apply_matches(conn, candidates, decisions, week="2026-W33")
    conn.commit()

    topic = db.get_canonical_topic_by_slug(conn, "export-to-csv")
    assert topic["name"] == "Export to CSV"
    assert topic["first_seen"] == "2026-W33"
    assert db.get_topic_weekly_mentions(conn, topic["topic_id"]) == {"2026-W33": 1}


def test_apply_matches_does_nothing_when_no_candidates(tmp_path):
    conn = seed_db(tmp_path)

    match.apply_matches(conn, [], [], week="2026-W33")
    conn.commit()

    assert db.get_canonical_topics(conn) == []


def test_build_matching_prompt_includes_topic_and_candidate_text(tmp_path):
    conn = seed_db(tmp_path)
    seed_existing_topic(conn)
    seed_candidate(conn, "User wants CSV export")

    existing = db.get_canonical_topics(conn)
    candidates = db.get_candidates_without_topic(conn)

    prompt = match.build_matching_prompt(candidates, existing)

    assert "Dark mode support" in prompt
    assert "User wants CSV export" in prompt


def test_call_matcher_returns_parsed_decisions():
    decisions = [{"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None}]
    client = FakeClient([json.dumps(decisions)])

    assert match.call_matcher(client, CANDIDATES, EXISTING_TOPICS) == decisions


def test_call_matcher_returns_empty_list_without_calling_api_when_no_candidates():
    client = FakeClient([])

    assert match.call_matcher(client, [], EXISTING_TOPICS) == []


def test_call_matcher_raises_api_error_on_api_failure():
    client = FakeClient([RuntimeError("rate limited")])

    with pytest.raises(match.MatchAPIError):
        match.call_matcher(client, CANDIDATES, EXISTING_TOPICS)


def test_call_matcher_raises_response_error_on_malformed_json():
    client = FakeClient(["not json at all"])

    with pytest.raises(match.MatchResponseError):
        match.call_matcher(client, CANDIDATES, EXISTING_TOPICS)


def test_match_error_subclasses_share_base_class():
    assert issubclass(match.MatchAPIError, match.MatchError)
    assert issubclass(match.MatchResponseError, match.MatchError)


def test_call_matcher_reads_text_block_after_thinking_block():
    response = FakeResponse(json.dumps([{"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None}]))
    response.content.insert(0, FakeThinkingBlock())
    client = FakeClient([response])

    decisions = match.call_matcher(client, CANDIDATES, EXISTING_TOPICS)

    assert decisions == [{"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None}]


def test_call_matcher_raises_response_error_when_no_text_block():
    response = FakeResponse("unused")
    response.content = [FakeThinkingBlock()]
    client = FakeClient([response])

    with pytest.raises(match.MatchResponseError, match="no text block"):
        match.call_matcher(client, CANDIDATES, EXISTING_TOPICS)


def test_apply_matches_groups_candidates_proposing_the_same_new_slug(tmp_path):
    conn = seed_db(tmp_path)
    first = seed_candidate(conn, "wants CSV export")
    second = seed_candidate(conn, "CSV export please")
    candidates = db.get_candidates_without_topic(conn)
    new_topic = {"name": "Export to CSV", "slug": "export-to-csv", "description": "CSV export"}
    decisions = [{"index": 0, "matched_topic_id": None, "new_topic": new_topic},
                 {"index": 1, "matched_topic_id": None, "new_topic": dict(new_topic)}]

    outcome = match.apply_matches(conn, candidates, decisions, week="2026-W33")
    conn.commit()

    topic = db.get_canonical_topic_by_slug(conn, "export-to-csv")
    assert len(db.get_canonical_topics(conn)) == 1
    assert len(outcome.created) == 1
    rows = conn.execute("SELECT candidate_id, topic_id FROM signal_candidate ORDER BY candidate_id").fetchall()
    assert [(r["candidate_id"], r["topic_id"]) for r in rows] == [(first, topic["topic_id"]), (second, topic["topic_id"])]
    assert db.get_topic_weekly_mentions(conn, topic["topic_id"]) == {"2026-W33": 2}


def test_apply_matches_applies_valid_decisions_and_records_invalid_ones(tmp_path):
    conn = seed_db(tmp_path)
    topic_id = seed_existing_topic(conn)
    good = seed_candidate(conn, "dark mode again")
    bad = seed_candidate(conn, "something")
    candidates = db.get_candidates_without_topic(conn)
    decisions = [{"index": 0, "matched_topic_id": topic_id}, {"index": 1, "matched_topic_id": "TOPIC-9999"}]

    outcome = match.apply_matches(conn, candidates, decisions, week="2026-W33")
    conn.commit()

    assert outcome.matched == 1
    assert outcome.invalid == [(bad, "unknown topic id 'TOPIC-9999'")]
    rows = {r["candidate_id"]: dict(r) for r in conn.execute(
        "SELECT candidate_id, topic_id, match_attempts, match_error FROM signal_candidate")}
    assert rows[good]["topic_id"] == topic_id
    assert (rows[bad]["topic_id"], rows[bad]["match_attempts"], rows[bad]["match_error"]) == (
        None, 1, "unknown topic id 'TOPIC-9999'")


def test_apply_matches_survives_a_missing_index_without_key_error(tmp_path):
    conn = seed_db(tmp_path)
    seed_candidate(conn, "a")
    candidates = db.get_candidates_without_topic(conn)

    outcome = match.apply_matches(conn, candidates, [], week="2026-W33")

    assert outcome.invalid[0][1] == "no decision"


def test_candidates_at_max_attempts_are_excluded(tmp_path):
    conn = seed_db(tmp_path)
    stuck = seed_candidate(conn, "a")
    fresh = seed_candidate(conn, "b")
    for _ in range(match.MAX_MATCH_ATTEMPTS):
        db.record_match_failure(conn, stuck, "bad")
    conn.commit()

    remaining = db.get_candidates_without_topic(conn, max_attempts=match.MAX_MATCH_ATTEMPTS)

    assert [c["candidate_id"] for c in remaining] == [fresh]
    assert len(db.get_candidates_without_topic(conn)) == 2


def test_get_candidates_by_ids_keeps_the_given_order(tmp_path):
    conn = seed_db(tmp_path)
    a = seed_candidate(conn, "a")
    b = seed_candidate(conn, "b")
    assert [c["candidate_id"] for c in db.get_candidates_by_ids(conn, [b, a])] == [b, a]
