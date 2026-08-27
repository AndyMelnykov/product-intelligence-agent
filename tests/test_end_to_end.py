import csv
import json
from datetime import date

import db
import extract
import fetch
import match
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
