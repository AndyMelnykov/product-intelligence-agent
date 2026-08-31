import json
import sys

import db
import query


def seed_db(tmp_path):
    db_path = tmp_path / "pi_agent.db"
    conn = db.connect(str(db_path))
    db.init_db(conn)
    evidence_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="https://reddit.com/x",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="Schema browser is slow", content="loading 20k tables takes forever", metadata={},
    )
    topic_id = db.insert_canonical_topic(
        conn, slug="slow-schema-loading", name="Slow schema loading",
        description="Users report poor performance", aliases=[],
        first_seen="2026-W30", last_seen="2026-W33",
    )
    db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="usability_issue",
        summary="Users report poor schema loading performance", confidence=0.9, topic_id=topic_id,
    )
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W33", amount=1)
    conn.commit()
    conn.close()
    return str(db_path)


def test_topic_command_prints_trend_and_evidence(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    conn = db.connect(db_path)

    query.topic_command(conn, "slow-schema-loading")

    conn.close()
    output = json.loads(capsys.readouterr().out)
    assert output["topic"]["name"] == "Slow schema loading"
    assert output["weekly_mentions"] == {"2026-W33": 1}
    assert output["evidence_urls"] == ["https://reddit.com/x"]


def test_topic_command_reports_unknown_slug(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    conn = db.connect(db_path)

    query.topic_command(conn, "does-not-exist")

    conn.close()
    assert "no topic found" in capsys.readouterr().out


def test_search_command_matches_keyword(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    conn = db.connect(db_path)

    query.search_command(conn, "schema loading")

    conn.close()
    output = json.loads(capsys.readouterr().out)
    assert len(output) == 1
    assert output[0]["signal_type"] == "usability_issue"


def test_main_topic_subcommand(tmp_path, monkeypatch, capsys):
    db_path = seed_db(tmp_path)
    monkeypatch.setattr(sys, "argv", ["query.py", "--db-path", db_path, "topic", "slow-schema-loading"])

    query.main()

    output = json.loads(capsys.readouterr().out)
    assert output["topic"]["slug"] == "slow-schema-loading"


def seed_material_signal(db_path):
    conn = db.connect(db_path)
    evidence_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="https://reddit.com/x",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="t", content="c", metadata={},
    )
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W33", last_seen="2026-W33",
    )
    signal_id = db.insert_material_signal(
        conn, created_at="2026-08-15T00:00:00+00:00", signal_type="new_feature_demand", topic_id=topic_id,
        entity={"type": "customer_topic", "topic_id": topic_id, "topic_name": "Dark mode support"},
        summary="Dark mode support: rising", confidence_label="high", materiality_label="high",
        materiality_score=0.8, materiality_reasons=["trend is rising"], evidence_ids=[evidence_id],
        change_type="trend_change", recommended_next_step="strategic_assessment",
    )
    conn.commit()
    conn.close()
    return signal_id


def test_signal_command_prints_material_signal(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    signal_id = seed_material_signal(db_path)
    conn = db.connect(db_path)

    query.signal_command(conn, signal_id)

    conn.close()
    output = json.loads(capsys.readouterr().out)
    assert output["signal_id"] == signal_id
    assert output["materiality_label"] == "high"


def test_signal_command_reports_unknown_id(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    conn = db.connect(db_path)

    query.signal_command(conn, "SIG-2026-9999")

    conn.close()
    assert "no material signal found" in capsys.readouterr().out
