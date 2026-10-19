import sqlite3

import pytest

import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def _topic(conn, slug="dark-mode", first_seen="2026-W30"):
    return db.insert_canonical_topic(conn, slug=slug, name="Dark mode", description="wants dark theme",
                                     aliases=[], first_seen=first_seen, last_seen=first_seen)


def _candidate(conn, topic_id=None, captured_at="2026-08-15T00:00:00+00:00"):
    evidence_id = db.insert_evidence(conn, source_type="reddit_post", source_name="s", source_url="/x",
                                     captured_at=captured_at, published_at=captured_at, title="t",
                                     content="c", metadata={})
    return db.insert_signal_candidate(conn, evidence_id=evidence_id, signal_type="new_feature_demand",
                                      summary="s", confidence=0.9, topic_id=topic_id)


def test_new_topic_gets_version_one(conn):
    topic_id = _topic(conn)
    [v1] = db.get_topic_versions(conn, topic_id)
    assert (v1["version"], v1["name"], v1["aliases"], v1["valid_from_week"], v1["reason"]) == (
        1, "Dark mode", [], "2026-W30", "created")


def test_update_topic_definition_writes_a_new_version_and_updates_latest(conn):
    topic_id = _topic(conn)

    version = db.update_topic_definition(conn, topic_id, week="2026-W35", reason="clarified",
                                         description="wants a dark UI theme", aliases=["night mode"])

    assert version == 2
    latest = db.get_canonical_topic_by_slug(conn, "dark-mode")
    assert (latest["name"], latest["description"], latest["aliases"]) == ("Dark mode", "wants a dark UI theme", ["night mode"])
    assert [v["description"] for v in db.get_topic_versions(conn, topic_id)] == ["wants dark theme", "wants a dark UI theme"]


def test_version_as_of_picks_the_latest_version_valid_that_week(conn):
    topic_id = _topic(conn)
    db.update_topic_definition(conn, topic_id, week="2026-W35", reason="r", name="Dark theme")

    assert db.get_topic_version_as_of(conn, topic_id, "2026-W29") is None
    assert db.get_topic_version_as_of(conn, topic_id, "2026-W34")["name"] == "Dark mode"
    assert db.get_topic_version_as_of(conn, topic_id, "2026-W35")["name"] == "Dark theme"


def test_get_canonical_topics_hides_merged_topics_by_default(conn):
    keep = _topic(conn, "keep")
    gone = _topic(conn, "gone")
    conn.execute("UPDATE canonical_topic SET status = 'merged', merged_into = ? WHERE topic_id = ?", (keep, gone))

    assert [t["topic_id"] for t in db.get_canonical_topics(conn)] == [keep]
    assert [t["topic_id"] for t in db.get_canonical_topics(conn, include_merged=True)] == [keep, gone]


def test_original_topic_is_set_once(conn):
    first = _topic(conn, "first")
    second = _topic(conn, "second")
    candidate_id = _candidate(conn)

    db.set_candidate_topic(conn, candidate_id, first)
    db.set_candidate_topic(conn, candidate_id, second)

    row = conn.execute("SELECT topic_id, original_topic_id FROM signal_candidate").fetchone()
    assert (row["topic_id"], row["original_topic_id"]) == (second, first)


def test_insert_with_topic_sets_original_topic(conn):
    topic_id = _topic(conn)
    _candidate(conn, topic_id=topic_id)
    assert conn.execute("SELECT original_topic_id FROM signal_candidate").fetchone()[0] == topic_id


def test_migration_backfills_versions_and_original_topics(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(db.SCHEMA_SQL)
    raw.execute("INSERT INTO canonical_topic VALUES ('TOPIC-0001', 'dark-mode', 'Dark mode', 'd', '[]', '2026-W30', '2026-W31')")
    raw.execute("INSERT INTO evidence (evidence_id, source_type, source_name, source_url, captured_at, published_at, "
                "title, content, metadata) VALUES ('EV-2026-000001', 'reddit_post', 's', '/x', "
                "'2026-08-15T00:00:00+00:00', '2026-08-15T00:00:00+00:00', 't', 'c', '{}')")
    raw.execute("INSERT INTO signal_candidate (candidate_id, evidence_id, signal_type, topic_id, summary, confidence) "
                "VALUES ('SC-2026-00001', 'EV-2026-000001', 'new_feature_demand', 'TOPIC-0001', 's', 0.9)")
    raw.commit()
    raw.close()

    conn = db.connect(str(path))
    db.init_db(conn)

    [v1] = db.get_topic_versions(conn, "TOPIC-0001")
    assert (v1["valid_from_week"], v1["reason"]) == ("2026-W30", "backfill")
    assert conn.execute("SELECT original_topic_id FROM signal_candidate").fetchone()[0] == "TOPIC-0001"
    assert db.get_canonical_topic_by_slug(conn, "dark-mode")["status"] == "active"


def test_derived_mentions_count_non_duplicate_candidates_by_captured_week(conn):
    topic_id = _topic(conn)
    _candidate(conn, topic_id, "2026-08-10T00:00:00+00:00")  # W33
    _candidate(conn, topic_id, "2026-08-15T00:00:00+00:00")  # W33
    dup = _candidate(conn, topic_id, "2026-08-20T00:00:00+00:00")  # W34, then marked duplicate
    evidence_id = conn.execute("SELECT evidence_id FROM signal_candidate WHERE candidate_id = ?", (dup,)).fetchone()[0]
    conn.execute("UPDATE evidence SET duplicate_of = 'EV-2026-000001' WHERE evidence_id = ?", (evidence_id,))

    assert db.derived_topic_weekly_mentions(conn, topic_id) == {"2026-W33": 2}


def test_refresh_replaces_cached_counts(conn):
    topic_id = _topic(conn)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=5)
    _candidate(conn, topic_id, "2026-08-15T00:00:00+00:00")

    db.refresh_topic_weekly_mentions(conn, [topic_id])

    assert db.get_topic_weekly_mentions(conn, topic_id) == {"2026-W33": 1}


def test_rebuild_all_reports_only_changed_topics(conn):
    changed = _topic(conn, "changed")
    same = _topic(conn, "same")
    db.increment_topic_weekly_mentions(conn, changed, "2026-W30", amount=2)
    _candidate(conn, changed, "2026-08-15T00:00:00+00:00")
    _candidate(conn, same, "2026-08-15T00:00:00+00:00")
    db.refresh_topic_weekly_mentions(conn, [same])

    diffs = db.rebuild_all_topic_weekly_mentions(conn)

    assert diffs == {changed: ({"2026-W30": 2}, {"2026-W33": 1})}
    assert db.get_topic_weekly_mentions(conn, changed) == {"2026-W33": 1}
