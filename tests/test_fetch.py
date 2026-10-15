import json
import sqlite3
from datetime import date

import prawcore
import pytest

import db
import fetch


class FakeErrorSubreddit:
    def __init__(self, error):
        self._error = error

    def new(self, limit):
        def gen():
            raise self._error
            yield  # pragma: no cover - unreachable, makes this a generator

        return gen()


class FakeResponse:
    def __init__(self, status_code, headers=None, text=""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text


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
    defaults = {
        "fullname": fullname,
        "id": fullname.split("_")[1],
        "title": "A title",
        "selftext": "Some body text",
        "permalink": f"/r/test/comments/{fullname}",
        "score": 10,
        "num_comments": 2,
        "created_utc": 1700000000.0,
    }
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


def test_fetch_new_posts_wraps_connection_error_as_fetch_error():
    error = prawcore.exceptions.RequestException(ConnectionError("boom"), (), {})
    client = FakeRedditClient({"sub": FakeErrorSubreddit(error)})

    with pytest.raises(fetch.FetchError, match="unavailable"):
        fetch.fetch_new_posts(client, "sub", last_seen_fullname=None, limit=10)


def test_fetch_new_posts_wraps_rate_limit_error_as_fetch_error():
    response = FakeResponse(429, headers={"retry-after": "30"})
    error = prawcore.exceptions.TooManyRequests(response)
    client = FakeRedditClient({"sub": FakeErrorSubreddit(error)})

    with pytest.raises(fetch.FetchError, match="rate-limited"):
        fetch.fetch_new_posts(client, "sub", last_seen_fullname=None, limit=10)


def test_fetch_new_posts_wraps_forbidden_error_as_fetch_error():
    response = FakeResponse(403)
    error = prawcore.exceptions.Forbidden(response)
    client = FakeRedditClient({"sub": FakeErrorSubreddit(error)})

    with pytest.raises(fetch.FetchError, match="auth"):
        fetch.fetch_new_posts(client, "sub", last_seen_fullname=None, limit=10)


def test_fetch_new_posts_wraps_server_error_as_fetch_error():
    response = FakeResponse(503)
    error = prawcore.exceptions.ServerError(response)
    client = FakeRedditClient({"sub": FakeErrorSubreddit(error)})

    with pytest.raises(fetch.FetchError, match="server error"):
        fetch.fetch_new_posts(client, "sub", last_seen_fullname=None, limit=10)


def test_fetch_new_posts_wraps_generic_prawcore_error_as_fetch_error():
    response = FakeResponse(404)
    error = prawcore.exceptions.NotFound(response)
    client = FakeRedditClient({"sub": FakeErrorSubreddit(error)})

    with pytest.raises(fetch.FetchError, match="sub"):
        fetch.fetch_new_posts(client, "sub", last_seen_fullname=None, limit=10)


def _run(tmp_path, client, subreddits, today=date(2026, 8, 15)):
    fetch.run(
        subreddits=subreddits, fetch_limit_per_subreddit=10,
        state_path=str(tmp_path / "state.json"), db_path=str(tmp_path / "pi_agent.db"),
        today=today, reddit_client=client,
    )


def _evidence(tmp_path):
    conn = db.connect(str(tmp_path / "pi_agent.db"))
    db.init_db(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT source_name, source_url, source_key, captured_at FROM evidence ORDER BY evidence_id"
    )]
    conn.close()
    return rows


def test_run_inserts_evidence_with_source_keys_and_stores_cursor_in_db(tmp_path):
    client = FakeRedditClient({
        "suba": FakeSubreddit([make_submission("t3_a1")]),
        "subb": FakeSubreddit([make_submission("t3_b1")]),
    })

    _run(tmp_path, client, ["suba", "subb"])

    assert _evidence(tmp_path) == [
        {"source_name": "suba", "source_url": "/r/test/comments/t3_a1", "source_key": "reddit_post:a1",
         "captured_at": "2026-08-15T00:00:00+00:00"},
        {"source_name": "subb", "source_url": "/r/test/comments/t3_b1", "source_key": "reddit_post:b1",
         "captured_at": "2026-08-15T00:00:00+00:00"},
    ]
    conn = db.connect(str(tmp_path / "pi_agent.db"))
    assert db.get_fetch_cursor(conn, "reddit_post", "suba") == "t3_a1"
    assert db.get_fetch_cursor(conn, "reddit_post", "subb") == "t3_b1"
    conn.close()
    assert not (tmp_path / "state.json").exists()


def test_run_does_not_duplicate_posts_when_the_cursor_post_was_deleted(tmp_path):
    first = FakeRedditClient({"sub": FakeSubreddit([make_submission("t3_p2"), make_submission("t3_p1")])})
    _run(tmp_path, first, ["sub"])

    # t3_p2 (the cursor) was deleted on Reddit, so the listing never reaches it again.
    second = FakeRedditClient({"sub": FakeSubreddit([make_submission("t3_p3"), make_submission("t3_p1")])})
    _run(tmp_path, second, ["sub"], today=date(2026, 8, 22))

    assert [r["source_key"] for r in _evidence(tmp_path)] == ["reddit_post:p2", "reddit_post:p1", "reddit_post:p3"]


def test_run_imports_legacy_state_file_cursor_when_db_has_none(tmp_path):
    with open(tmp_path / "state.json", "w", encoding="utf-8") as f:
        json.dump({"sub": "t3_old"}, f)
    client = FakeRedditClient({"sub": FakeSubreddit([make_submission("t3_new"), make_submission("t3_old")])})

    _run(tmp_path, client, ["sub"])

    assert [r["source_key"] for r in _evidence(tmp_path)] == ["reddit_post:new"]


def test_run_keeps_healthy_subreddits_and_raises_naming_the_failed_one(tmp_path):
    response = FakeResponse(503)
    client = FakeRedditClient({
        "suba": FakeSubreddit([make_submission("t3_a1")]),
        "subb": FakeErrorSubreddit(prawcore.exceptions.ServerError(response)),
        "subc": FakeSubreddit([make_submission("t3_c1")]),
    })

    with pytest.raises(fetch.FetchError, match="subb"):
        _run(tmp_path, client, ["suba", "subb", "subc"])

    assert [r["source_key"] for r in _evidence(tmp_path)] == ["reddit_post:a1", "reddit_post:c1"]
    conn = db.connect(str(tmp_path / "pi_agent.db"))
    assert db.get_fetch_cursor(conn, "reddit_post", "subb") is None
    conn.close()


def test_run_keeps_earlier_subreddits_when_a_later_one_fails_unexpectedly(tmp_path, monkeypatch):
    def failing_fetch(client, subreddit_name, last_seen_fullname, limit):
        if subreddit_name == "subb":
            raise RuntimeError("bug")
        return [{"id": "a1", "title": "A title", "selftext": "body", "permalink": "/r/test/comments/t3_a1",
                 "score": 10, "num_comments": 2, "created_utc": 1700000000.0}], "t3_a1"

    monkeypatch.setattr(fetch, "fetch_new_posts", failing_fetch)

    with pytest.raises(RuntimeError):
        _run(tmp_path, FakeRedditClient({}), ["suba", "subb"])

    assert [r["source_key"] for r in _evidence(tmp_path)] == ["reddit_post:a1"]


def test_insert_evidence_if_new_returns_none_for_a_known_source_key():
    conn = db.connect(":memory:")
    db.init_db(conn)
    fields = dict(source_type="reddit_post", source_name="sub", source_url="/a",
                  captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
                  title="t", content="c", metadata={})

    first = db.insert_evidence_if_new(conn, source_key="reddit_post:a", **fields)
    second = db.insert_evidence_if_new(conn, source_key="reddit_post:a", **fields)

    assert first == "EV-2026-000001"
    assert second is None


def test_migration_marks_existing_duplicates(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(db.SCHEMA_SQL)
    for evidence_id, post_id in [("EV-2026-000001", "p1"), ("EV-2026-000002", "p1"), ("EV-2026-000003", "p2")]:
        raw.execute(
            "INSERT INTO evidence (evidence_id, source_type, source_name, source_url, captured_at, published_at, "
            "title, content, metadata) VALUES (?, 'reddit_post', 'sub', ?, '2026-08-15T00:00:00+00:00', "
            "'2026-08-14T00:00:00+00:00', 't', 'c', ?)",
            (evidence_id, f"/{post_id}", json.dumps({"reddit_post_id": post_id})),
        )
    raw.commit()
    raw.close()

    conn = db.connect(str(path))
    db.init_db(conn)

    rows = [dict(r) for r in conn.execute("SELECT evidence_id, source_key, duplicate_of FROM evidence ORDER BY evidence_id")]
    assert rows == [
        {"evidence_id": "EV-2026-000001", "source_key": "reddit_post:p1", "duplicate_of": None},
        {"evidence_id": "EV-2026-000002", "source_key": None, "duplicate_of": "EV-2026-000001"},
        {"evidence_id": "EV-2026-000003", "source_key": "reddit_post:p2", "duplicate_of": None},
    ]
