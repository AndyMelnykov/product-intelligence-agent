import json

import pytest

import db
import registry
from fingerprints import fingerprint


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


def test_registry_as_of_shows_topics_and_definitions_as_they_were(conn):
    keep = topic(conn, "dark-mode", "Dark mode", first_seen="2026-W30")
    topic(conn, "night-theme", "Night theme", first_seen="2026-W31")
    source = topic(conn, "exports", "Exports", first_seen="2026-W30")
    first = candidate(conn, source)
    candidate(conn, source)
    registry.merge_topics(conn, "night-theme", "dark-mode", week="2026-W36", reason="r", decided_by="h")
    registry.split_topic(conn, "exports", new_name="CSV", new_slug="csv", new_description="d",
                         candidate_ids=[first], week="2026-W37", reason="r", decided_by="h")

    before = registry.registry_as_of(conn, "2026-W35")
    after = registry.registry_as_of(conn, "2026-W37")

    assert [t["slug"] for t in before] == ["dark-mode", "night-theme", "exports"]
    assert next(t for t in before if t["topic_id"] == keep)["aliases"] == []
    assert [t["slug"] for t in after] == ["dark-mode", "exports", "csv"]
    assert next(t for t in after if t["topic_id"] == keep)["aliases"] == ["Night theme"]


def test_registry_fingerprint_ignores_order_and_tracks_definitions():
    a = [{"topic_id": "T1", "name": "A", "description": "d", "aliases": []},
         {"topic_id": "T2", "name": "B", "description": "d", "aliases": []}]
    assert registry.registry_fingerprint(a) == registry.registry_fingerprint(list(reversed(a)))
    changed = [dict(a[0], description="other"), a[1]]
    assert registry.registry_fingerprint(a) != registry.registry_fingerprint(changed)
    assert registry.registry_fingerprint([]) == fingerprint([])


def test_topic_history_collects_versions_lineage_and_mentions(conn):
    topic(conn, "dark-mode", "Dark mode")
    topic(conn, "night-theme", "Night theme")
    registry.merge_topics(conn, "night-theme", "dark-mode", week="2026-W36", reason="r", decided_by="h")

    history = registry.topic_history(conn, "dark-mode")

    assert [v["version"] for v in history["versions"]] == [1, 2]
    assert history["lineage"][0]["kind"] == "merge"
    assert history["weekly_mentions"] == {}


def test_cli_merge_commits_and_history_prints_json(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    c = db.connect(path)
    db.init_db(c)
    topic(c, "dark-mode", "Dark mode")
    topic(c, "night-theme", "Night theme")
    c.commit()
    c.close()

    assert registry.main(["--db-path", path, "merge", "night-theme", "dark-mode", "--reason", "same"]) == 0
    capsys.readouterr()
    assert registry.main(["--db-path", path, "history", "dark-mode"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["topic"]["aliases"] == ["Night theme"]
    assert output["lineage"][0]["decided_by"] == "human:cli"


def test_cli_reports_registry_errors_with_exit_code_2(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    assert registry.main(["--db-path", path, "merge", "a", "b", "--reason", "r"]) == 2
    assert "no topic" in capsys.readouterr().err


def test_cli_rebuild_mentions_is_a_dry_run_unless_apply(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    c = db.connect(path)
    db.init_db(c)
    topic_id = topic(c, "dark-mode", "Dark mode")
    db.increment_topic_weekly_mentions(c, topic_id, "2026-W30", amount=3)
    c.commit()
    c.close()

    registry.main(["--db-path", path, "rebuild-mentions"])
    assert "would change 1 topic" in capsys.readouterr().out
    c = db.connect(path)
    assert db.get_topic_weekly_mentions(c, topic_id) == {"2026-W30": 3}
    c.close()

    registry.main(["--db-path", path, "rebuild-mentions", "--apply"])
    c = db.connect(path)
    assert db.get_topic_weekly_mentions(c, topic_id) == {}
