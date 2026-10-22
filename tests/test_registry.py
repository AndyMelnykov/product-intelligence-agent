import pytest

import db
import registry


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def topic(conn, slug, name, first_seen="2026-W30"):
    return db.insert_canonical_topic(conn, slug=slug, name=name, description=f"{name} description",
                                     aliases=[], first_seen=first_seen, last_seen=first_seen)


def candidate(conn, topic_id, captured_at="2026-08-15T00:00:00+00:00", n=[0]):
    n[0] += 1
    evidence_id = db.insert_evidence(conn, source_type="reddit_post", source_name="s", source_url=f"/{n[0]}",
                                     captured_at=captured_at, published_at=captured_at, title="t", content="c", metadata={})
    candidate_id = db.insert_signal_candidate(conn, evidence_id=evidence_id, signal_type="new_feature_demand",
                                              summary="s", confidence=0.9, topic_id=topic_id)
    db.refresh_topic_weekly_mentions(conn, [topic_id])
    return candidate_id


def test_merge_moves_candidates_retires_the_source_and_records_lineage(conn):
    keep = topic(conn, "dark-mode", "Dark mode")
    gone = topic(conn, "night-theme", "Night theme", first_seen="2026-W28")
    moved = candidate(conn, gone, "2026-07-08T00:00:00+00:00")  # W28
    candidate(conn, keep)

    result = registry.merge_topics(conn, "night-theme", "dark-mode", week="2026-W40",
                                   reason="same request", decided_by="human:test")

    assert result == {"from": gone, "into": keep, "moved": [moved]}
    source = db.get_canonical_topic_by_slug(conn, "night-theme")
    assert (source["status"], source["merged_into"]) == ("merged", keep)
    survivor = db.get_canonical_topic_by_slug(conn, "dark-mode")
    assert survivor["aliases"] == ["Night theme"]
    assert survivor["first_seen"] == "2026-W28"
    assert db.get_topic_weekly_mentions(conn, keep) == {"2026-W28": 1, "2026-W33": 1}
    assert db.get_topic_weekly_mentions(conn, gone) == {}
    [lineage] = db.get_lineage_for_topic(conn, gone)
    assert (lineage["kind"], lineage["to_topic_id"], lineage["candidate_ids"]) == ("merge", keep, [moved])
    row = conn.execute("SELECT topic_id, original_topic_id FROM signal_candidate WHERE candidate_id = ?", (moved,)).fetchone()
    assert (row["topic_id"], row["original_topic_id"]) == (keep, gone)


@pytest.mark.parametrize("from_slug,into_slug,message", [
    ("missing", "dark-mode", "no topic"),
    ("dark-mode", "dark-mode", "itself"),
])
def test_merge_rejects_bad_requests(conn, from_slug, into_slug, message):
    topic(conn, "dark-mode", "Dark mode")
    with pytest.raises(registry.RegistryError, match=message):
        registry.merge_topics(conn, from_slug, into_slug, week="2026-W40", reason="r", decided_by="h")


def test_merge_rejects_an_already_merged_topic(conn):
    keep = topic(conn, "a", "A")
    topic(conn, "b", "B")
    topic(conn, "c", "C")
    registry.merge_topics(conn, "b", "a", week="2026-W40", reason="r", decided_by="h")
    with pytest.raises(registry.RegistryError, match="already merged"):
        registry.merge_topics(conn, "b", "c", week="2026-W40", reason="r", decided_by="h")
    assert db.get_merge_redirects(conn) == {db.get_canonical_topic_by_slug(conn, "b")["topic_id"]: keep}


def test_split_moves_the_named_candidates_into_a_new_topic(conn):
    source = topic(conn, "exports", "Exports")
    csv_one = candidate(conn, source, "2026-08-10T00:00:00+00:00")
    candidate(conn, source)
    csv_two = candidate(conn, source, "2026-08-20T00:00:00+00:00")

    result = registry.split_topic(conn, "exports", new_name="Export to CSV", new_slug="Export to CSV",
                                  new_description="CSV specifically", candidate_ids=[csv_one, csv_two],
                                  week="2026-W40", reason="two issues", decided_by="human:test")

    new = db.get_canonical_topic_by_slug(conn, "export-to-csv")
    assert result == {"from": source, "new": new["topic_id"], "moved": [csv_one, csv_two]}
    assert (new["first_seen"], new["last_seen"]) == ("2026-W33", "2026-W34")
    assert db.get_topic_versions(conn, new["topic_id"])[0]["valid_from_week"] == "2026-W40"
    assert db.get_topic_weekly_mentions(conn, source) == {"2026-W33": 1}


def test_split_rejects_candidates_from_another_topic_or_moving_everything(conn):
    source = topic(conn, "exports", "Exports")
    other = topic(conn, "other", "Other")
    only = candidate(conn, source)
    foreign = candidate(conn, other)
    with pytest.raises(registry.RegistryError, match="not in topic"):
        registry.split_topic(conn, "exports", new_name="X", new_slug="x", new_description="d",
                             candidate_ids=[foreign], week="2026-W40", reason="r", decided_by="h")
    with pytest.raises(registry.RegistryError, match="every candidate"):
        registry.split_topic(conn, "exports", new_name="X", new_slug="x", new_description="d",
                             candidate_ids=[only], week="2026-W40", reason="r", decided_by="h")
