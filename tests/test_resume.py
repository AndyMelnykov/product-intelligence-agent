"""A week of the pipeline interrupted at every stage boundary must end in the same state as an
uninterrupted run, without repeating paid work beyond the one call that was in flight."""
import json
from datetime import date

import pytest

import db
import extract
import fetch
import match
import materiality


class SimulatedCrash(BaseException):
    pass


class Submission:
    def __init__(self, i):
        self.fullname, self.id = f"t3_p{i}", f"p{i}"
        self.title, self.selftext = "Please add dark mode", f"post {i}"
        self.permalink = f"/r/sub/comments/p{i}"
        self.score, self.num_comments, self.created_utc = 5, 1, 1700000000.0 + i


class Subreddit:
    def __init__(self, n):
        self._subs = [Submission(i) for i in range(n, 0, -1)]

    def new(self, limit):
        return iter(self._subs[:limit])


class Reddit:
    def __init__(self, n):
        self._sub = Subreddit(n)

    def subreddit(self, name):
        return self._sub


class Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class Response:
    def __init__(self, text):
        self.content = [Text(text)]
        self.stop_reason = "end_turn"


class Messages:
    def __init__(self, responses, crash_on=None):
        self._responses, self._crash_on, self.calls = list(responses), crash_on, 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == self._crash_on:
            raise SimulatedCrash()
        return Response(self._responses.pop(0))


class Client:
    def __init__(self, responses, crash_on=None):
        self.messages = Messages(responses, crash_on)


EXTRACTED = json.dumps({"signal_type": "new_feature_demand", "summary": "wants dark mode", "confidence": 0.9})
MATCHED = json.dumps([{"index": i, "matched_topic_id": None,
                       "new_topic": {"name": "Dark mode support", "slug": "dark-mode-support", "description": "d"}}
                      for i in range(3)])
TODAY = date(2026, 8, 15)


def test_interrupted_week_converges_to_the_uninterrupted_result(tmp_path):
    db_path, state_path, events = str(tmp_path / "pi.db"), str(tmp_path / "state.json"), str(tmp_path / "ev.jsonl")

    # Fetch twice (a re-run after a crash right after fetch): no duplicates.
    for _ in range(2):
        fetch.run(["sub"], 10, state_path=state_path, db_path=db_path, today=TODAY, reddit_client=Reddit(3))

    # Extraction dies on its second call.
    crashing = Client([EXTRACTED], crash_on=2)
    with pytest.raises(SimulatedCrash):
        extract.run(db_path=db_path, client=crashing)
    resumed = Client([EXTRACTED, EXTRACTED])
    extract.run(db_path=db_path, client=resumed)
    assert crashing.messages.calls + resumed.messages.calls == 4  # 3 posts + 1 repeated in-flight call

    match.run(db_path=db_path, today=TODAY, client=Client([MATCHED]))
    match.run(db_path=db_path, today=TODAY, client=Client([]))  # nothing left: no call

    materiality.run(db_path=db_path, today=TODAY, events_path=events)
    materiality.run(db_path=db_path, today=TODAY, events_path=events)

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM signal_candidate").fetchone()[0] == 3
    topic = db.get_canonical_topic_by_slug(conn, "dark-mode-support")
    assert db.get_topic_weekly_mentions(conn, topic["topic_id"]) == {"2026-W33": 3}
    health = db.pipeline_health(conn, max_match_attempts=match.MAX_MATCH_ATTEMPTS)
    assert health["llm_calls_pending"] == 0
    assert health["llm_calls_abandoned"] == 1
    assert health["events_unflushed"] == 0
