import csv
import json
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

import config
import db
import publish

WEEK = "2026-W33"
CAPTURED = "2026-08-15T00:00:00+00:00"
BASE_PUBLISHED = datetime(2026, 8, 14, 0, 0, tzinfo=timezone.utc)


def open_db(tmp_path):
    conn = db.connect(str(tmp_path / "pi_agent.db"))
    db.init_db(conn)
    return conn


def add_topic(conn, slug="dark-mode-support", name="Dark mode support", material=True, week=WEEK):
    topic_id = db.insert_canonical_topic(
        conn, slug=slug, name=name, description="d", aliases=[],
        first_seen="2026-W30", last_seen=week,
    )
    if material:
        add_material_signal(conn, topic_id, week)
    return topic_id


def add_material_signal(conn, topic_id, week=WEEK):
    return db.insert_material_signal(
        conn, created_at="2026-08-15T12:00:00+00:00", signal_type="new_feature_demand",
        topic_id=topic_id,
        entity={"type": "customer_topic", "topic_id": topic_id, "topic_name": "t"},
        summary="rising", confidence_label="high", materiality_label="high",
        materiality_score=0.8, materiality_reasons=["r"], evidence_ids=[],
        change_type="trend_change", recommended_next_step="strategic_assessment", week=week,
    )


def add_post(conn, topic_id, i, *, captured_at=CAPTURED, source_url=None, title="Dark mode",
             content="Please add it.", summary="Users want dark mode", signal_type="new_feature_demand"):
    """One evidence row plus its candidate; published_at is stepped one hour per `i`."""
    published_at = (BASE_PUBLISHED + timedelta(hours=i)).isoformat()
    evidence_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub",
        source_url=f"https://www.reddit.com/r/sub/comments/p{i}/" if source_url is None else source_url,
        captured_at=captured_at, published_at=published_at, title=title, content=content, metadata={},
    )
    candidate_id = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type=signal_type, summary=summary,
        confidence=0.9, topic_id=topic_id,
    )
    return evidence_id, candidate_id


def test_non_material_topic_publishes_nothing(tmp_path):
    conn = open_db(tmp_path)
    quiet = add_topic(conn, slug="quiet", name="Quiet", material=False)
    loud = add_topic(conn, slug="loud", name="Loud")
    add_post(conn, quiet, 0, source_url="https://example.com/quiet")
    add_post(conn, loud, 1, source_url="https://example.com/loud")
    conn.commit()

    rows = publish.collect_signals(conn, WEEK, 10)

    assert [r["source_ref"] for r in rows] == ["https://example.com/loud"]


def test_cap_and_duplicates_newest_first(tmp_path):
    conn = open_db(tmp_path)
    topic_id = add_topic(conn)
    evidence_ids = [add_post(conn, topic_id, i)[0] for i in range(25)]
    for i in (22, 23, 24):  # the newest three are duplicates of the oldest
        conn.execute("UPDATE evidence SET duplicate_of = ? WHERE evidence_id = ?",
                     (evidence_ids[0], evidence_ids[i]))
    conn.commit()

    rows = publish.collect_signals(conn, WEEK, 10)

    assert [r["source_ref"] for r in rows] == [
        f"https://www.reddit.com/r/sub/comments/p{i}/" for i in range(21, 11, -1)
    ]
    dates = [r["date"] for r in rows]
    assert dates == sorted(dates, reverse=True)


def test_skipped_extraction_is_excluded(tmp_path):
    conn = open_db(tmp_path)
    topic_id = add_topic(conn)
    skipped_evidence, _ = add_post(conn, topic_id, 0, source_url="https://example.com/skipped")
    add_post(conn, topic_id, 1, source_url="https://example.com/kept")
    db.record_extraction_outcome(conn, skipped_evidence, "skipped", None, decided_by="triage")
    conn.commit()

    rows = publish.collect_signals(conn, WEEK, 10)

    assert [r["source_ref"] for r in rows] == ["https://example.com/kept"]


def test_other_weeks_are_excluded(tmp_path):
    conn = open_db(tmp_path)
    topic_id = add_topic(conn)
    add_post(conn, topic_id, 0, captured_at="2026-08-08T00:00:00+00:00",
             source_url="https://example.com/last-week")
    add_post(conn, topic_id, 1, source_url="https://example.com/this-week")
    conn.commit()

    rows = publish.collect_signals(conn, WEEK, 10)

    assert [r["source_ref"] for r in rows] == ["https://example.com/this-week"]


def test_field_limits_and_no_internal_ids(tmp_path):
    conn = open_db(tmp_path)
    topic_id = add_topic(conn)
    add_post(conn, topic_id, 0, summary="word " * 80, content="x" * 5000)
    add_post(conn, topic_id, 1)
    conn.commit()

    rows = publish.collect_signals(conn, WEEK, 10)

    assert len(rows) == 2
    for row in rows:
        assert len(row["summary"]) <= publish.SUMMARY_MAX
        assert len(row["detail"]) <= publish.DETAIL_MAX
        assert "account" not in row
        assert row["source_type"] == "community"
        assert row["tags"] == "dark-mode-support;new_feature_demand"
        blob = json.dumps(row)
        for internal in ("EV-", "SC-", "SIG-2026"):
            assert internal not in blob
    long_row = next(r for r in rows if len(r["detail"]) > 1000)
    assert len(long_row["detail"]) == publish.DETAIL_MAX
    truncated = next(r for r in rows if r["summary"].endswith("…"))
    assert len(truncated["summary"]) == publish.SUMMARY_MAX
    assert "  " not in truncated["summary"]


def test_usernames_and_emails_are_scrubbed(tmp_path):
    conn = open_db(tmp_path)
    topic_id = add_topic(conn)
    add_post(conn, topic_id, 0, summary="u/jdoe wants dark mode",
             content="thanks u/jdoe and /u/jdoe, mail a.b@example.com")
    conn.commit()

    (row,) = publish.collect_signals(conn, WEEK, 10)

    blob = json.dumps(row)
    assert "jdoe" not in blob
    assert "@example.com" not in blob
    assert row["summary"] == "u/[user] wants dark mode"
    assert "u/[user] and u/[user]" in row["detail"]
    assert "[email]" in row["detail"]


def test_relative_permalink_becomes_absolute_url(tmp_path):
    conn = open_db(tmp_path)
    topic_id = add_topic(conn)
    add_post(conn, topic_id, 0, source_url="/r/sub/comments/abc/t/")
    add_post(conn, topic_id, 1, source_url="https://example.com/already/absolute")
    conn.commit()

    refs = {r["source_ref"] for r in publish.collect_signals(conn, WEEK, 10)}

    assert refs == {
        "https://www.reddit.com/r/sub/comments/abc/t/",
        "https://example.com/already/absolute",
    }


def test_merged_or_topicless_material_signal_publishes_nothing(tmp_path):
    conn = open_db(tmp_path)
    old = add_topic(conn, slug="old", name="Old")
    new = add_topic(conn, slug="new", name="New", material=False)
    add_post(conn, old, 0)
    db.move_candidates(conn, old, new)
    db.set_topic_status(conn, old, "merged", merged_into=new)
    add_material_signal(conn, None)  # topic_id NULL
    conn.commit()

    assert publish.material_topic_ids(conn, WEEK) == [old]
    assert publish.collect_signals(conn, WEEK, 10) == []


def test_none_title_and_content_are_safe(tmp_path):
    conn = open_db(tmp_path)
    topic_id = add_topic(conn)
    add_post(conn, topic_id, 0, title=None, content=None)
    conn.commit()

    (row,) = publish.collect_signals(conn, WEEK, 10)

    assert row["detail"] == ""


# --- run: hand rows to the TeamOSTemplate checkout's signals.py ---------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_SCRIPT = REPO_ROOT / "tests" / "fixtures" / "teamos" / "scripts" / "signals" / "signals.py"
TODAY = date(2026, 8, 15)


def make_teamos_tree(tmp_path) -> Path:
    root = tmp_path / "TeamOS"
    script_dir = root / "scripts" / "signals"
    script_dir.mkdir(parents=True)
    shutil.copy(VENDORED_SCRIPT, script_dir / "signals.py")
    store = root / "product-development" / "signals" / "signals.csv"
    store.parent.mkdir(parents=True)
    store.write_bytes(b"")
    return root


def read_signals(root) -> list[dict]:
    path = Path(root) / "product-development" / "signals" / "signals.csv"
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def teamos_config(root) -> dict:
    return {"repo_path": str(root), "max_signals_per_topic": 10}


def seeded_db(tmp_path, posts=3, **post_fields) -> str:
    conn = open_db(tmp_path)
    topic_id = add_topic(conn)
    for i in range(posts):
        add_post(conn, topic_id, i, **post_fields)
    conn.commit()
    conn.close()
    return str(tmp_path / "pi_agent.db")


def test_vendored_signals_py_matches_repo_path_when_configured():
    config_path = REPO_ROOT / "config.yaml"
    if not config_path.exists():
        pytest.skip("no config.yaml")
    teamos = config.load_config(str(config_path))["teamos"]
    if teamos is None:
        pytest.skip("config.yaml has no teamos section")
    real = Path(teamos["repo_path"]) / publish.SIGNALS_SCRIPT
    assert real.read_text(encoding="utf-8") == VENDORED_SCRIPT.read_text(encoding="utf-8"), (
        f"tests/fixtures/teamos signals.py has drifted from {real}; re-copy the file"
    )


def test_publish_adds_community_rows_for_material_topic(tmp_path):
    root = make_teamos_tree(tmp_path)
    db_path = seeded_db(tmp_path)

    result = publish.run(teamos_config(root), db_path=db_path, today=TODAY)

    rows = read_signals(root)
    assert len(rows) == 3
    assert {r["source_type"] for r in rows} == {"community"}
    assert all(r["source_ref"] for r in rows)
    assert result["week"] == WEEK
    assert result["written"] == result["selected"] == 3


def test_second_publish_adds_nothing(tmp_path, capsys):
    root = make_teamos_tree(tmp_path)
    db_path = seeded_db(tmp_path)
    publish.run(teamos_config(root), db_path=db_path, today=TODAY)
    capsys.readouterr()

    result = publish.run(teamos_config(root), db_path=db_path, today=TODAY)

    assert len(read_signals(root)) == 3
    assert result["written"] == 0
    assert result["skipped"] == 3
    assert "0 new" in capsys.readouterr().out


def test_missing_signals_py_names_path_and_writes_nothing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    db_path = tmp_path / "never.db"

    with pytest.raises(publish.PublishError) as excinfo:
        publish.run(teamos_config(empty), db_path=str(db_path), today=TODAY)

    assert str(empty / publish.SIGNALS_SCRIPT) in str(excinfo.value)
    assert not db_path.exists()
    assert not (empty / "product-development").exists()


def test_only_signals_py_is_started(tmp_path, monkeypatch):
    root = make_teamos_tree(tmp_path)
    db_path = seeded_db(tmp_path)
    real_run, calls = subprocess.run, []

    def recording_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(publish.subprocess, "run", recording_run)

    publish.run(teamos_config(root), db_path=db_path, today=TODAY)

    assert len(calls) == 1
    assert calls[0][1].endswith("signals.py")
    assert not any(Path(arg).name.lower() in ("git", "git.exe") for arg in calls[0])


def test_nothing_material_starts_no_subprocess(tmp_path, monkeypatch, capsys):
    root = make_teamos_tree(tmp_path)
    conn = open_db(tmp_path)
    add_post(conn, add_topic(conn, material=False), 0)
    conn.commit()
    conn.close()
    calls = []
    monkeypatch.setattr(publish.subprocess, "run", lambda *a, **k: calls.append(a))

    result = publish.run(teamos_config(root), db_path=str(tmp_path / "pi_agent.db"), today=TODAY)

    assert calls == []
    assert result["selected"] == 0
    assert "nothing to publish" in capsys.readouterr().out


def test_non_ascii_text_survives_to_the_csv(tmp_path):
    root = make_teamos_tree(tmp_path)
    text = "dark mode 🌙 “quotes” 日本語"
    db_path = seeded_db(tmp_path, posts=1, content=text)

    publish.run(teamos_config(root), db_path=db_path, today=TODAY)

    (row,) = read_signals(root)
    assert text in row["detail"]


def test_rejected_row_is_shown_and_exit_is_nonzero(tmp_path, capsys):
    root = make_teamos_tree(tmp_path)
    conn = open_db(tmp_path)
    topic_id = add_topic(conn)
    add_post(conn, topic_id, 0, source_url="")
    add_post(conn, topic_id, 1, source_url="https://example.com/valid")
    conn.commit()
    conn.close()

    with pytest.raises(publish.PublishError):
        publish.run(teamos_config(root), db_path=str(tmp_path / "pi_agent.db"), today=TODAY)

    assert [r["source_ref"] for r in read_signals(root)] == ["https://example.com/valid"]
    assert "error: row" in capsys.readouterr().err


def test_publish_without_teamos_raises():
    with pytest.raises(publish.PublishError, match=r"teamos\.repo_path"):
        publish.run(None)
