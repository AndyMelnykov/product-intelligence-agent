import csv
import json

import report


def make_topic(topic_id, name, first_seen, weekly_mentions):
    return {"topic_id": topic_id, "name": name, "first_seen": first_seen, "weekly_mentions": weekly_mentions}


def test_compute_trends_marks_first_seen_this_week_as_new():
    topics = [make_topic("TOPIC-0001", "New topic", "2026-W33", {"2026-W33": 1})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "new"
    assert rows[0]["mentions_this_week"] == 1


def test_compute_trends_marks_rising_when_above_recent_average():
    topics = [make_topic("TOPIC-0001", "Dark mode", "2026-W30",
                          {"2026-W30": 2, "2026-W31": 2, "2026-W32": 2, "2026-W33": 10})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "rising"


def test_compute_trends_marks_falling_when_below_recent_average():
    topics = [make_topic("TOPIC-0001", "Dark mode", "2026-W30",
                          {"2026-W30": 10, "2026-W31": 10, "2026-W32": 10, "2026-W33": 1})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "falling"


def test_compute_trends_marks_stable_within_band():
    topics = [make_topic("TOPIC-0001", "Dark mode", "2026-W30",
                          {"2026-W30": 5, "2026-W31": 5, "2026-W32": 5, "2026-W33": 5})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "stable"


def test_compute_trends_weights_recent_weeks_more_heavily():
    # Same math as before the migration: flat average would call this "falling",
    # the recency-weighted average keeps it "stable".
    topics = [make_topic("TOPIC-0001", "Dark mode", "2026-W29",
                          {"2026-W30": 10, "2026-W31": 3, "2026-W32": 1, "2026-W33": 3})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "stable"


def test_compute_trends_sorts_by_mentions_this_week_descending():
    topics = [
        make_topic("TOPIC-LOW", "Low", "2026-W33", {"2026-W33": 1}),
        make_topic("TOPIC-HIGH", "High", "2026-W33", {"2026-W33": 9}),
    ]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert [r["id"] for r in rows] == ["TOPIC-HIGH", "TOPIC-LOW"]


def test_write_report_produces_matching_json_and_csv(tmp_path):
    rows = [{"id": "TOPIC-0001", "canonical_name": "Dark mode", "mentions_this_week": 5,
             "total_mentions": 12, "trend": "rising"}]
    json_path = tmp_path / "reports" / "2026-W33.json"
    csv_path = tmp_path / "reports" / "2026-W33.csv"

    report.write_report(rows, str(json_path), str(csv_path))

    with open(json_path, "r", encoding="utf-8") as f:
        assert json.load(f) == rows

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = list(csv.DictReader(f))
    assert reader[0]["canonical_name"] == "Dark mode"
    assert reader[0]["trend"] == "rising"
    assert "category" not in reader[0]
