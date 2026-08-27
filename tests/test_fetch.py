import json
import os
from datetime import date

import pytest

import db
import fetch


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


def make_submission(fullname, **overrides):
    defaults = dict(
        fullname=fullname,
        id=fullname.split("_")[1],
        title="A title",
        selftext="Some body text",
        permalink=f"/r/test/comments/{fullname}",
        score=10,
        num_comments=2,
        created_utc=1700000000.0,
    )
    defaults.update(overrides)
    return FakeSubmission(**defaults)


def test_fetch_new_posts_maps_expected_fields():
    submissions = [make_submission("t3_new1")]
    client = FakeRedditClient({"sub": FakeSubreddit(submissions)})

    posts, newest = fetch.fetch_new_posts(client, "sub", last_seen_fullname=None, limit=10)

    assert posts == [{
        "id": "new1",
        "title": "A title",
        "selftext": "Some body text",
        "permalink": "/r/test/comments/t3_new1",
        "score": 10,
        "num_comments": 2,
        "created_utc": 1700000000.0,
    }]
    assert newest == "t3_new1"


def test_fetch_new_posts_stops_at_last_seen():
    submissions = [make_submission("t3_new2"), make_submission("t3_old1")]
    client = FakeRedditClient({"sub": FakeSubreddit(submissions)})

    posts, newest = fetch.fetch_new_posts(client, "sub", last_seen_fullname="t3_old1", limit=10)

    assert [p["id"] for p in posts] == ["new2"]
    assert newest == "t3_new2"


def test_fetch_new_posts_returns_empty_when_no_new_posts():
    submissions = [make_submission("t3_old1")]
    client = FakeRedditClient({"sub": FakeSubreddit(submissions)})

    posts, newest = fetch.fetch_new_posts(client, "sub", last_seen_fullname="t3_old1", limit=10)

    assert posts == []
    assert newest == "t3_old1"


def test_fetch_new_posts_skips_removed_and_deleted():
    submissions = [
        make_submission("t3_new3", title="[deleted]", selftext="[deleted]"),
        make_submission("t3_new4", selftext="[removed]"),
        make_submission("t3_new5"),
    ]
    client = FakeRedditClient({"sub": FakeSubreddit(submissions)})

    posts, newest = fetch.fetch_new_posts(client, "sub", last_seen_fullname=None, limit=10)

    assert [p["id"] for p in posts] == ["new5"]
    assert newest == "t3_new3"


def test_run_inserts_evidence_rows_and_updates_state(tmp_path):
    submissions = {
        "suba": FakeSubreddit([make_submission("t3_a1")]),
        "subb": FakeSubreddit([make_submission("t3_b1")]),
    }
    client = FakeRedditClient(submissions)
    state_path = tmp_path / "state.json"
    db_path = tmp_path / "pi_agent.db"

    fetch.run(
        subreddits=["suba", "subb"],
        fetch_limit_per_subreddit=10,
        state_path=str(state_path),
        db_path=str(db_path),
        today=date(2026, 8, 15),
        reddit_client=client,
    )

    conn = db.connect(str(db_path))
    rows = conn.execute(
        "SELECT source_name, source_url, title, captured_at FROM evidence ORDER BY evidence_id"
    ).fetchall()
    conn.close()

    assert [dict(r) for r in rows] == [
        {"source_name": "suba", "source_url": "/r/test/comments/t3_a1", "title": "A title",
         "captured_at": "2026-08-15T00:00:00+00:00"},
        {"source_name": "subb", "source_url": "/r/test/comments/t3_b1", "title": "A title",
         "captured_at": "2026-08-15T00:00:00+00:00"},
    ]

    with open(state_path, "r", encoding="utf-8") as f:
        assert json.load(f) == {"suba": "t3_a1", "subb": "t3_b1"}


def test_run_leaves_state_and_db_untouched_when_a_subreddit_fetch_fails(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"suba": "t3_old"}, f)
    db_path = tmp_path / "pi_agent.db"

    def failing_fetch(client, subreddit_name, last_seen_fullname, limit):
        if subreddit_name == "subb":
            raise RuntimeError("Reddit API error")
        return [{
            "id": "a1", "title": "A title", "selftext": "body",
            "permalink": "/r/test/comments/t3_a1", "score": 10, "num_comments": 2,
            "created_utc": 1700000000.0,
        }], "t3_a1"

    monkeypatch.setattr(fetch, "fetch_new_posts", failing_fetch)

    with pytest.raises(RuntimeError):
        fetch.run(
            subreddits=["suba", "subb"],
            fetch_limit_per_subreddit=10,
            state_path=str(state_path),
            db_path=str(db_path),
            today=date(2026, 8, 15),
            reddit_client=FakeRedditClient({}),
        )

    with open(state_path, "r", encoding="utf-8") as f:
        assert json.load(f) == {"suba": "t3_old"}

    conn = db.connect(str(db_path))
    db.init_db(conn)
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
    conn.close()
