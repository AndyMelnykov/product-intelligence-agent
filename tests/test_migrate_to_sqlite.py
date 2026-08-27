from pathlib import Path

import db
import migrate_to_sqlite

FIXTURES = Path(__file__).parent / "fixtures"


def test_migrate_creates_topic_with_weekly_mentions_and_placeholder_evidence(tmp_path):
    db_path = tmp_path / "pi_agent.db"

    migrate_to_sqlite.migrate(str(FIXTURES / "registry_sample.json"), str(db_path))

    conn = db.connect(str(db_path))
    topic = db.get_canonical_topic_by_slug(conn, "dark-mode-support")
    assert topic["name"] == "Dark mode support"
    assert topic["first_seen"] == "2026-W30"
    assert topic["last_seen"] == "2026-W31"

    mentions = db.get_topic_weekly_mentions(conn, topic["topic_id"])
    assert mentions == {"2026-W30": 2, "2026-W31": 3}

    evidence = db.get_evidence_for_topic(conn, topic["topic_id"])
    assert evidence[0]["source_url"] == "https://reddit.com/r/example/comments/abc"
    assert evidence[0]["metadata"] == {"migrated": True}
    conn.close()


def test_migrate_maps_legacy_category_to_signal_type(tmp_path):
    db_path = tmp_path / "pi_agent.db"

    migrate_to_sqlite.migrate(str(FIXTURES / "registry_sample.json"), str(db_path))

    conn = db.connect(str(db_path))
    topic = db.get_canonical_topic_by_slug(conn, "dark-mode-support")
    candidate = conn.execute(
        "SELECT signal_type FROM signal_candidate WHERE topic_id = ?", (topic["topic_id"],)
    ).fetchone()
    conn.close()

    assert candidate["signal_type"] == "new_feature_demand"  # fixture's category is "feature_request"
