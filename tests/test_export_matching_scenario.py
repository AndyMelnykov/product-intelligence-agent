import yaml

import db
from evals import export_matching_scenario, harness


def seed(path):
    conn = db.connect(path)
    db.init_db(conn)
    old = db.insert_canonical_topic(conn, slug="dark-mode", name="Dark mode", description="wants dark theme",
                                    aliases=[], first_seen="2026-W30", last_seen="2026-W30")
    new = db.insert_canonical_topic(conn, slug="csv", name="CSV export", description="wants CSV",
                                    aliases=[], first_seen="2026-W33", last_seen="2026-W33")
    for i, (topic_id, captured) in enumerate([(old, "2026-08-15"), (new, "2026-08-15"), (old, "2026-07-27")]):
        evidence_id = db.insert_evidence(conn, source_type="reddit_post", source_name="s", source_url=f"/{i}",
                                         captured_at=f"{captured}T00:00:00+00:00", published_at=f"{captured}T00:00:00+00:00",
                                         title="t", content="c", metadata={})
        db.insert_signal_candidate(conn, evidence_id=evidence_id, signal_type="new_feature_demand",
                                   summary=f"summary {i}", confidence=0.9, topic_id=topic_id)
    conn.commit()
    conn.close()
    return old


def test_build_scenario_uses_the_previous_weeks_registry_and_this_weeks_candidates(tmp_path):
    path = str(tmp_path / "pi.db")
    old = seed(path)
    conn = db.connect(path)

    scenario = export_matching_scenario.build_scenario(conn, "2026-W33", limit=30)

    assert scenario["draft"] is True
    assert scenario["name"] == "prod_2026_W33"
    assert [t["topic_id"] for t in scenario["existing_topics"]] == [old]
    assert [c["summary"] for c in scenario["candidates"]] == ["summary 0", "summary 1"]
    assert scenario["expected"] == [
        {"index": 0, "matched_topic_id": old, "new_topic": None},
        {"index": 1, "matched_topic_id": None, "new_topic": None},
    ]


def test_main_writes_a_file_the_loader_refuses_until_draft_is_removed(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    seed(path)
    out_dir = tmp_path / "golden"
    out_dir.mkdir()
    out = out_dir / "prod.yaml"

    assert export_matching_scenario.main(["--db-path", path, "--week", "2026-W33", "--out", str(out)]) == 0

    try:
        harness.load_golden_dir(out_dir, "matching")
        raise AssertionError("draft scenario should be rejected")
    except harness.GoldenSchemaError as e:
        assert "draft scenario" in str(e)

    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    del data["draft"]
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    [scenario] = harness.load_golden_dir(out_dir, "matching")
    assert scenario.name == "prod_2026_W33"


def test_main_refuses_a_week_with_no_matched_candidates(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    seed(path)
    assert export_matching_scenario.main(["--db-path", path, "--week", "2026-W40",
                                          "--out", str(tmp_path / "x.yaml")]) == 2
    assert "no matched candidates" in capsys.readouterr().err
