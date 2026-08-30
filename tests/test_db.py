import sqlite3

import pytest

import db


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_db(connection)
    yield connection
    connection.close()


def _insert_sample_evidence(conn, **overrides):
    defaults = dict(
        source_type="reddit_post", source_name="yourproductname",
        source_url="https://reddit.com/r/example/comments/abc",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T10:00:00+00:00",
        title="Please add dark mode", content="Would love a dark theme",
        metadata={"score": 10, "num_comments": 2},
    )
    defaults.update(overrides)
    return db.insert_evidence(conn, **defaults)


def test_init_db_creates_all_tables(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"evidence", "canonical_topic", "signal_candidate", "topic_weekly_mentions", "material_signal"} <= tables


def test_insert_evidence_returns_sequential_ids_scoped_by_year(conn):
    first = _insert_sample_evidence(conn)
    second = _insert_sample_evidence(conn)
    third = _insert_sample_evidence(conn, captured_at="2027-01-01T00:00:00+00:00")

    assert first == "EV-2026-000001"
    assert second == "EV-2026-000002"
    assert third == "EV-2027-000001"


def test_get_evidence_round_trips_metadata_json(conn):
    evidence_id = _insert_sample_evidence(conn, metadata={"score": 42, "num_comments": 7})

    evidence = db.get_evidence(conn, evidence_id)

    assert evidence["metadata"] == {"score": 42, "num_comments": 7}
    assert evidence["title"] == "Please add dark mode"


def test_get_evidence_returns_none_for_unknown_id(conn):
    assert db.get_evidence(conn, "EV-2026-999999") is None


def test_insert_signal_candidate_derives_year_from_linked_evidence(conn):
    evidence_id = _insert_sample_evidence(conn, captured_at="2025-03-01T00:00:00+00:00")

    candidate_id = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand",
        summary="User wants dark mode", confidence=0.9,
    )

    assert candidate_id == "SC-2025-00001"


def test_insert_signal_candidate_rejects_unknown_evidence_id(conn):
    with pytest.raises(db.DBError, match="does not exist"):
        db.insert_signal_candidate(
            conn, evidence_id="EV-2026-000001", signal_type="new_feature_demand",
            summary="orphaned", confidence=0.5,
        )


def test_get_evidence_without_candidate_excludes_processed_rows(conn):
    processed = _insert_sample_evidence(conn)
    pending = _insert_sample_evidence(conn, title="A second post")
    db.insert_signal_candidate(
        conn, evidence_id=processed, signal_type="new_feature_demand", summary="s", confidence=0.8,
    )

    remaining = db.get_evidence_without_candidate(conn)

    assert [e["evidence_id"] for e in remaining] == [pending]


def test_insert_canonical_topic_returns_sequential_ids_without_year(conn):
    first = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )
    second = db.insert_canonical_topic(
        conn, slug="export-to-csv", name="Export to CSV", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )

    assert first == "TOPIC-0001"
    assert second == "TOPIC-0002"


def test_insert_canonical_topic_rejects_duplicate_slug(conn):
    db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )

    with pytest.raises(db.DBError):
        db.insert_canonical_topic(
            conn, slug="dark-mode-support", name="Dark mode support (dup)", description="d",
            aliases=[], first_seen="2026-W31", last_seen="2026-W31",
        )


def test_get_candidates_without_topic_excludes_matched_rows(conn):
    evidence_id = _insert_sample_evidence(conn)
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )
    matched = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand",
        summary="s", confidence=0.8, topic_id=topic_id,
    )
    unmatched = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="usability_issue", summary="s2", confidence=0.7,
    )

    remaining = db.get_candidates_without_topic(conn)

    assert [c["candidate_id"] for c in remaining] == [unmatched]


def test_increment_topic_weekly_mentions_creates_then_accumulates(conn):
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )

    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=1)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=1)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W31", amount=3)

    assert db.get_topic_weekly_mentions(conn, topic_id) == {"2026-W30": 2, "2026-W31": 3}


def test_get_evidence_for_topic_returns_linked_evidence_only(conn):
    linked_evidence = _insert_sample_evidence(conn)
    other_evidence = _insert_sample_evidence(conn, title="unrelated")
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )
    db.insert_signal_candidate(
        conn, evidence_id=linked_evidence, signal_type="new_feature_demand",
        summary="s", confidence=0.9, topic_id=topic_id,
    )
    db.insert_signal_candidate(
        conn, evidence_id=other_evidence, signal_type="new_feature_demand", summary="s2", confidence=0.5,
    )

    linked = db.get_evidence_for_topic(conn, topic_id)

    assert [e["evidence_id"] for e in linked] == [linked_evidence]


def test_search_evidence_and_candidates_matches_title_content_or_summary(conn):
    evidence_id = _insert_sample_evidence(conn, title="Schema browser is slow", content="loading 20k tables")
    db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="usability_issue",
        summary="Users report poor schema loading performance", confidence=0.85,
    )

    by_title = db.search_evidence_and_candidates(conn, "schema browser")
    by_summary = db.search_evidence_and_candidates(conn, "loading performance")
    no_match = db.search_evidence_and_candidates(conn, "pricing")

    assert len(by_title) == 1
    assert len(by_summary) == 1
    assert no_match == []


def test_insert_material_signal_returns_sequential_ids_scoped_by_year(conn):
    evidence_id = _insert_sample_evidence(conn)
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W33",
    )

    def _insert(created_at):
        return db.insert_material_signal(
            conn, created_at=created_at, signal_type="new_feature_demand", topic_id=topic_id,
            entity={"type": "customer_topic", "topic_id": topic_id, "topic_name": "Dark mode support"},
            summary="Dark mode support: rising", confidence_label="high",
            materiality_label="high", materiality_score=0.8,
            materiality_reasons=["trend is rising with 5 mentions this week"],
            evidence_ids=[evidence_id], change_type="trend_change",
            recommended_next_step="strategic_assessment",
        )

    first = _insert("2026-08-23T14:32:00+00:00")
    second = _insert("2026-08-23T15:00:00+00:00")
    third = _insert("2027-01-05T00:00:00+00:00")

    assert first == "SIG-2026-0001"
    assert second == "SIG-2026-0002"
    assert third == "SIG-2027-0001"


def test_get_material_signal_round_trips_json_fields(conn):
    evidence_id = _insert_sample_evidence(conn)
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W33",
    )
    signal_id = db.insert_material_signal(
        conn, created_at="2026-08-23T14:32:00+00:00", signal_type="new_feature_demand", topic_id=topic_id,
        entity={"type": "customer_topic", "topic_id": topic_id, "topic_name": "Dark mode support"},
        summary="Dark mode support: rising", confidence_label="high",
        materiality_label="high", materiality_score=0.8,
        materiality_reasons=["trend is rising with 5 mentions this week"],
        evidence_ids=[evidence_id], change_type="trend_change",
        recommended_next_step="strategic_assessment",
    )

    signal = db.get_material_signal(conn, signal_id)

    assert signal["signal_id"] == signal_id
    assert signal["entity"] == {"type": "customer_topic", "topic_id": topic_id, "topic_name": "Dark mode support"}
    assert signal["materiality_reasons"] == ["trend is rising with 5 mentions this week"]
    assert signal["evidence_ids"] == [evidence_id]
    assert signal["materiality_label"] == "high"


def test_get_material_signal_returns_none_for_unknown_id(conn):
    assert db.get_material_signal(conn, "SIG-2026-9999") is None


def test_get_candidates_for_topic_returns_joined_rows_ordered_by_candidate_id(conn):
    evidence_id = _insert_sample_evidence(conn, captured_at="2026-08-15T00:00:00+00:00")
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W33", last_seen="2026-W33",
    )
    candidate_id = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand",
        summary="User wants dark mode", confidence=0.9, topic_id=topic_id,
    )

    candidates = db.get_candidates_for_topic(conn, topic_id)

    assert candidates == [{
        "candidate_id": candidate_id, "evidence_id": evidence_id, "signal_type": "new_feature_demand",
        "summary": "User wants dark mode", "confidence": 0.9, "captured_at": "2026-08-15T00:00:00+00:00",
    }]
