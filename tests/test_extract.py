import json
from datetime import date

import pytest

import db
import extract


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
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


SAMPLE_EVIDENCE = {
    "evidence_id": "EV-2026-000001",
    "source_type": "reddit_post",
    "source_name": "yourproductname",
    "source_url": "/r/test/comments/abc123",
    "captured_at": "2026-08-15T00:00:00+00:00",
    "published_at": "2026-08-14T10:00:00+00:00",
    "title": "Would love dark mode",
    "content": "Please add a dark theme, my eyes hurt at night.",
    "metadata": {"reddit_post_id": "abc123", "score": 42, "num_comments": 5},
}


def test_extract_topic_parses_valid_response():
    client = FakeClient([
        json.dumps({"signal_type": "new_feature_demand", "summary": "User wants a dark theme", "confidence": 0.9})
    ])

    result = extract.extract_topic(client, SAMPLE_EVIDENCE)

    assert result == {"signal_type": "new_feature_demand", "summary": "User wants a dark theme", "confidence": 0.9}


def test_extract_topic_returns_none_on_skip_flag():
    client = FakeClient([json.dumps({"skip": True})])

    assert extract.extract_topic(client, SAMPLE_EVIDENCE) is None


def test_extract_topic_raises_on_invalid_signal_type():
    client = FakeClient([
        json.dumps({"signal_type": "not_a_real_type", "summary": "y", "confidence": 0.5})
    ])

    with pytest.raises(extract.ExtractionError):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


def test_extract_topic_raises_on_malformed_json():
    client = FakeClient(["not json at all"])

    with pytest.raises(extract.ExtractionError):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


def test_extract_topic_raises_on_api_error():
    client = FakeClient([RuntimeError("rate limited")])

    with pytest.raises(extract.ExtractionError):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


def test_run_creates_candidates_for_pending_evidence_and_skips_bad_ones(tmp_path):
    db_path = tmp_path / "pi_agent.db"
    conn = db.connect(str(db_path))
    db.init_db(conn)
    good_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="/a",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="Good post", content="body", metadata={},
    )
    db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="/b",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="Bad post", content="body", metadata={},
    )
    conn.commit()
    conn.close()

    client = FakeClient([
        json.dumps({"signal_type": "new_feature_demand", "summary": "s", "confidence": 0.8}),
        "not json",
    ])

    extract.run(db_path=str(db_path), today=date(2026, 8, 15), client=client)

    conn = db.connect(str(db_path))
    rows = conn.execute("SELECT evidence_id, signal_type, summary FROM signal_candidate").fetchall()
    conn.close()

    assert [dict(r) for r in rows] == [{"evidence_id": good_id, "signal_type": "new_feature_demand", "summary": "s"}]
