import db
import match


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
