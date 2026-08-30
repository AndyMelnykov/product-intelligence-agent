import csv
import json
from datetime import date

import db
import extract
import fetch
import match
import materiality
import report


class FakeSubmission:
    def __init__(self, fullname, id, title, selftext, permalink, score, num_comments, created_utc):
        self.fullname = fullname
        self.id = id
        self.title = title
        self.selftext = selftext
        self.permalink = permalink
        self.score = score
        self.num_comments = num_comments
        self.created_utc = created_utc


class FakeSubreddit:
    def __init__(self, submissions):
        self._submissions = submissions

    def new(self, limit):
        return iter(self._submissions[:limit])


class FakeRedditClient:
    def __init__(self, subreddits):
        self._subreddits = subreddits

    def subreddit(self, name):
        return self._subreddits[name]


class FakeContentBlock:
    def __init__(self, text):
        self.text = text


class FakeResponse:
    def __init__(self, text):
        self.content = [FakeContentBlock(text)]


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        return FakeResponse(self._responses.pop(0))


class FakeAnthropicClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


def test_full_pipeline_from_fetch_to_report(tmp_path):
    reddit_client = FakeRedditClient({
        "yourproductname": FakeSubreddit([
            FakeSubmission(
                fullname="t3_p1", id="p1", title="Please add dark mode",
                selftext="Would love a dark theme", permalink="/r/yourproductname/comments/p1",
                score=20, num_comments=4, created_utc=1700000000.0,
            ),
        ]),
    })

    extract_client = FakeAnthropicClient([
        json.dumps({"signal_type": "new_feature_demand", "summary": "User wants dark theme", "confidence": 0.9}),
    ])
    match_client = FakeAnthropicClient([
        json.dumps([{
            "index": 0, "matched_topic_id": None,
            "new_topic": {"name": "Dark mode support", "slug": "dark-mode-support",
                          "description": "Users requesting a dark theme option"},
        }]),
    ])

    state_path = tmp_path / "state.json"
    db_path = tmp_path / "pi_agent.db"
    report_dir = tmp_path / "reports"
    today = date(2026, 8, 15)

    fetch.run(
        subreddits=["yourproductname"], fetch_limit_per_subreddit=10,
        state_path=str(state_path), db_path=str(db_path), today=today, reddit_client=reddit_client,
    )
    extract.run(db_path=str(db_path), today=today, client=extract_client)
    match.run(db_path=str(db_path), today=today, client=match_client)
    report.run(db_path=str(db_path), report_dir=str(report_dir), trend_window_weeks=8, today=today)

    conn = db.connect(str(db_path))
    topic = db.get_canonical_topic_by_slug(conn, "dark-mode-support")
    assert topic["name"] == "Dark mode support"
    assert db.get_topic_weekly_mentions(conn, topic["topic_id"]) == {"2026-W33": 1}
    conn.close()

    with open(report_dir / "2026-W33.json", "r", encoding="utf-8") as f:
        report_rows = json.load(f)
    assert report_rows[0]["canonical_name"] == "Dark mode support"
    assert report_rows[0]["trend"] == "new"

    with open(report_dir / "2026-W33.csv", "r", encoding="utf-8", newline="") as f:
        csv_rows = list(csv.DictReader(f))
    assert csv_rows[0]["canonical_name"] == "Dark mode support"


def test_pipeline_emits_material_signal_when_topic_crosses_high_threshold(tmp_path):
    state_path = tmp_path / "state.json"
    db_path = tmp_path / "pi_agent.db"
    events_path = tmp_path / "events.jsonl"

    week1_client = FakeRedditClient({
        "yourproductname": FakeSubreddit([
            FakeSubmission(
                fullname="t3_p1", id="p1", title="Please add dark mode",
                selftext="Would love a dark theme", permalink="/r/yourproductname/comments/p1",
                score=20, num_comments=4, created_utc=1700000000.0,
            ),
        ]),
    })
    fetch.run(
        subreddits=["yourproductname"], fetch_limit_per_subreddit=10,
        state_path=str(state_path), db_path=str(db_path), today=date(2026, 8, 15),
        reddit_client=week1_client,
    )
    extract.run(db_path=str(db_path), today=date(2026, 8, 15), client=FakeAnthropicClient([
        json.dumps({"signal_type": "new_feature_demand", "summary": "User wants dark theme", "confidence": 0.9}),
    ]))
    match.run(db_path=str(db_path), today=date(2026, 8, 15), client=FakeAnthropicClient([
        json.dumps([{
            "index": 0, "matched_topic_id": None,
            "new_topic": {"name": "Dark mode support", "slug": "dark-mode-support",
                          "description": "Users requesting a dark theme option"},
        }]),
    ]))
    materiality.run(db_path=str(db_path), today=date(2026, 8, 15), events_path=str(events_path))

    conn = db.connect(str(db_path))
    assert conn.execute("SELECT COUNT(*) FROM material_signal").fetchone()[0] == 0
    conn.close()
    assert not events_path.exists()

    week2_posts = [
        FakeSubmission(
            fullname=f"t3_p{i}", id=f"p{i}", title="More dark mode requests",
            selftext="Still no dark theme", permalink=f"/r/yourproductname/comments/p{i}",
            score=5, num_comments=1, created_utc=1700500000.0,
        )
        for i in range(2, 8)
    ]
    week2_client = FakeRedditClient({"yourproductname": FakeSubreddit(week2_posts)})
    fetch.run(
        subreddits=["yourproductname"], fetch_limit_per_subreddit=10,
        state_path=str(state_path), db_path=str(db_path), today=date(2026, 8, 22),
        reddit_client=week2_client,
    )
    extract.run(db_path=str(db_path), today=date(2026, 8, 22), client=FakeAnthropicClient([
        json.dumps({"signal_type": "new_feature_demand", "summary": "User wants dark theme", "confidence": 0.9})
        for _ in range(6)
    ]))
    match.run(db_path=str(db_path), today=date(2026, 8, 22), client=FakeAnthropicClient([
        json.dumps([{"index": i, "matched_topic_id": "TOPIC-0001", "new_topic": None} for i in range(6)]),
    ]))
    materiality.run(db_path=str(db_path), today=date(2026, 8, 22), events_path=str(events_path))

    conn = db.connect(str(db_path))
    signals = conn.execute("SELECT signal_id, materiality_label FROM material_signal").fetchall()
    conn.close()
    assert len(signals) == 1
    assert signals[0]["materiality_label"] == "high"

    with open(events_path, "r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f]
    assert len(events) == 1
    assert events[0]["event"] == "product_intelligence.signal.material"
    assert events[0]["signal"]["materiality"] == "high"
