import argparse

import db
from registry import load_registry

LEGACY_CATEGORY_TO_SIGNAL_TYPE = {
    "feature_request": "new_feature_demand",
    "complaint": "complaint_rising",
    "praise": "positive_adoption_pattern",
    "question": "usability_issue",
    "competitor_comparison": "competitor_mention_rising",
}


def _slugify(name):
    return "-".join(name.lower().split())


def migrate(registry_path, db_path):
    registry = load_registry(registry_path)
    conn = db.connect(db_path)
    db.init_db(conn)

    try:
        for topic in registry["topics"]:
            weeks = sorted(topic["weekly_mentions"])
            first_seen = topic.get("first_seen_week", weeks[0] if weeks else "")
            last_seen = weeks[-1] if weeks else first_seen
            signal_type = LEGACY_CATEGORY_TO_SIGNAL_TYPE.get(topic.get("category"), "new_feature_demand")

            topic_id = db.insert_canonical_topic(
                conn,
                slug=_slugify(topic["canonical_name"]),
                name=topic["canonical_name"],
                description=topic["description"],
                aliases=[],
                first_seen=first_seen,
                last_seen=last_seen,
            )

            for week, mentions in topic["weekly_mentions"].items():
                db.increment_topic_weekly_mentions(conn, topic_id, week, amount=mentions)

            for permalink in topic["example_permalinks"]:
                evidence_id = db.insert_evidence(
                    conn,
                    source_type="reddit_post",
                    source_name="migrated",
                    source_url=permalink,
                    captured_at=f"{last_seen[:4]}-01-01T00:00:00+00:00",
                    published_at=f"{last_seen[:4]}-01-01T00:00:00+00:00",
                    title=None,
                    content=None,
                    metadata={"migrated": True},
                )
                db.insert_signal_candidate(
                    conn,
                    evidence_id=evidence_id,
                    signal_type=signal_type,
                    summary=f"Migrated from legacy registry topic {topic['canonical_name']!r}",
                    confidence=1.0,
                    topic_id=topic_id,
                )
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="One-time migration from registry.json to SQLite")
    parser.add_argument("--registry-path", default="data/registry.json")
    parser.add_argument("--db-path", default="data/pi_agent.db")
    args = parser.parse_args()
    migrate(args.registry_path, args.db_path)


if __name__ == "__main__":
    main()
