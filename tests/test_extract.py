import json
import sqlite3
from datetime import date

import pytest

import db
import extract
import ledger
import triage as triage_module


class FakeContentBlock:
    type = "text"

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
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(result)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


class FakeThinkingBlock:
    type = "thinking"
    thinking = ""


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

    assert result == {
        "signal_type": "new_feature_demand", "summary": "User wants a dark theme", "confidence": 0.9,
        "entity": None, "effective_date": None,
    }


def test_extract_topic_parses_entity_and_effective_date_when_present():
    client = FakeClient([
        json.dumps({
            "signal_type": "competitor_mention_rising", "summary": "Users comparing to Competitor X",
            "confidence": 0.8,
            "entity": {"type": "competitor_product", "company": "Competitor X", "product": "Product Y"},
            "effective_date": "2027-05-31",
        })
    ])

    result = extract.extract_topic(client, SAMPLE_EVIDENCE)

    assert result["entity"] == {"type": "competitor_product", "company": "Competitor X", "product": "Product Y"}
    assert result["effective_date"] == "2027-05-31"


def test_extract_topic_coerces_non_dict_entity_to_none():
    client = FakeClient([
        json.dumps({
            "signal_type": "competitor_mention_rising", "summary": "Users comparing to Competitor X",
            "confidence": 0.8,
            "entity": "Competitor X",
        })
    ])

    result = extract.extract_topic(client, SAMPLE_EVIDENCE)

    assert result["entity"] is None


def test_extract_topic_returns_none_on_skip_flag():
    client = FakeClient([json.dumps({"skip": True})])

    assert extract.extract_topic(client, SAMPLE_EVIDENCE) is None


def test_extract_topic_raises_response_error_on_invalid_signal_type():
    client = FakeClient([
        json.dumps({"signal_type": "not_a_real_type", "summary": "y", "confidence": 0.5})
    ])

    with pytest.raises(extract.ExtractionResponseError):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


def test_extract_topic_raises_response_error_on_malformed_json():
    client = FakeClient(["not json at all"])

    with pytest.raises(extract.ExtractionResponseError):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


def test_extract_topic_raises_response_error_on_missing_keys():
    client = FakeClient([json.dumps({"signal_type": "new_feature_demand"})])

    with pytest.raises(extract.ExtractionResponseError):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


def test_extract_topic_raises_api_error_on_api_failure():
    client = FakeClient([RuntimeError("rate limited")])

    with pytest.raises(extract.ExtractionAPIError):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


def test_extraction_error_subclasses_share_base_class():
    assert issubclass(extract.ExtractionAPIError, extract.ExtractionError)
    assert issubclass(extract.ExtractionResponseError, extract.ExtractionError)


def test_extract_topic_parses_evidence_without_evidence_id():
    client = FakeClient([
        json.dumps({"signal_type": "new_feature_demand", "summary": "User wants a dark theme", "confidence": 0.9})
    ])
    evidence = {"title": "Would love dark mode", "content": "Please add a dark theme."}

    result = extract.extract_topic(client, evidence)

    assert result["signal_type"] == "new_feature_demand"


def test_extract_topic_raises_extraction_error_not_key_error_without_evidence_id():
    client = FakeClient(["not json at all"])
    evidence = {"title": "Would love dark mode", "content": "Please add a dark theme."}

    with pytest.raises(extract.ExtractionResponseError):
        extract.extract_topic(client, evidence)


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


def test_run_persists_entity_and_effective_date_from_extraction(tmp_path):
    db_path = tmp_path / "pi_agent.db"
    conn = db.connect(str(db_path))
    db.init_db(conn)
    evidence_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="/c",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="Competitor X sunsetting Product Y", content="body", metadata={},
    )
    conn.commit()
    conn.close()

    client = FakeClient([
        json.dumps({
            "signal_type": "competitor_mention_rising", "summary": "s", "confidence": 0.9,
            "entity": {"type": "competitor_product", "company": "Competitor X", "product": "Product Y"},
            "effective_date": "2027-05-31",
        }),
    ])

    extract.run(db_path=str(db_path), today=date(2026, 8, 15), client=client)

    conn = db.connect(str(db_path))
    row = conn.execute(
        "SELECT entity, effective_date FROM signal_candidate WHERE evidence_id = ?", (evidence_id,)
    ).fetchone()
    conn.close()

    assert json.loads(row["entity"]) == {
        "type": "competitor_product", "company": "Competitor X", "product": "Product Y",
    }
    assert row["effective_date"] == "2027-05-31"


def test_extract_topic_reads_text_block_after_thinking_block():
    response = FakeResponse(json.dumps(
        {"signal_type": "new_feature_demand", "summary": "User wants a dark theme", "confidence": 0.9}
    ))
    response.content.insert(0, FakeThinkingBlock())
    client = FakeClient([response])

    result = extract.extract_topic(client, SAMPLE_EVIDENCE)

    assert result["signal_type"] == "new_feature_demand"


def test_extract_topic_raises_response_error_when_no_text_block():
    response = FakeResponse("unused")
    response.content = [FakeThinkingBlock()]
    client = FakeClient([response])

    with pytest.raises(extract.ExtractionResponseError, match="no text block"):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


@pytest.mark.parametrize("raw", ['["a"]', '"skip"', "42"])
def test_extract_topic_raises_response_error_for_non_object_json(raw):
    client = FakeClient([raw])

    with pytest.raises(extract.ExtractionResponseError, match="expected a JSON object"):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


def test_extract_topic_raises_response_error_for_non_string_signal_type():
    client = FakeClient([json.dumps({"signal_type": ["reliability_issue"], "summary": "x", "confidence": 0.5})])

    with pytest.raises(extract.ExtractionResponseError, match="invalid signal_type"):
        extract.extract_topic(client, SAMPLE_EVIDENCE)


class SimulatedCrash(BaseException):
    """Stands in for the process dying: not an Exception, so no handler in the code catches it."""


class CrashOnCall:
    def __init__(self, responses, crash_on):
        self._responses = list(responses)
        self._crash_on = crash_on
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if self.calls == self._crash_on:
            raise SimulatedCrash()
        return FakeResponse(self._responses.pop(0))


def _seed(tmp_path, n=1, **overrides):
    db_path = str(tmp_path / "pi_agent.db")
    conn = db.connect(db_path)
    db.init_db(conn)
    ids = []
    for i in range(n):
        fields = dict(source_type="reddit_post", source_name="sub", source_url=f"/{i}",
                      captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
                      title=f"post {i}", content="body", metadata={})
        fields.update(overrides)
        ids.append(db.insert_evidence(conn, **fields))
    conn.commit()
    conn.close()
    return db_path, ids


VALID = json.dumps({"signal_type": "new_feature_demand", "summary": "s", "confidence": 0.8})


def test_skipped_evidence_is_recorded_and_never_sent_again(tmp_path):
    db_path, [evidence_id] = _seed(tmp_path)

    extract.run(db_path=db_path, client=FakeClient([json.dumps({"skip": True})]))
    extract.run(db_path=db_path, client=FakeClient([]))  # would raise IndexError if it called the API

    conn = db.connect(db_path)
    assert db.get_extraction_state(conn, evidence_id)["status"] == "skipped"
    assert conn.execute("SELECT COUNT(*) FROM signal_candidate").fetchone()[0] == 0


def test_candidate_records_provenance_to_its_llm_call(tmp_path):
    db_path, [evidence_id] = _seed(tmp_path)

    extract.run(db_path=db_path, client=FakeClient([VALID]))

    conn = db.connect(db_path)
    row = conn.execute("SELECT llm_call_id FROM signal_candidate WHERE evidence_id = ?", (evidence_id,)).fetchone()
    call = ledger.get_call(conn, row["llm_call_id"])
    assert call["stage"] == "extract"
    assert call["subject_key"] == evidence_id
    assert call["status"] == "succeeded"
    assert call["raw_response"] == VALID
    assert call["model"] == extract.EXTRACTION_MODEL


def test_unparseable_responses_retry_then_go_dead(tmp_path):
    db_path, [evidence_id] = _seed(tmp_path)

    for _ in range(extract.MAX_EXTRACTION_ATTEMPTS):
        extract.run(db_path=db_path, client=FakeClient(["not json"]))
    extract.run(db_path=db_path, client=FakeClient([]))  # dead rows are not retried

    conn = db.connect(db_path)
    state = db.get_extraction_state(conn, evidence_id)
    assert (state["status"], state["attempts"]) == ("dead", extract.MAX_EXTRACTION_ATTEMPTS)


def test_api_errors_are_retryable_and_do_not_count_as_attempts(tmp_path):
    db_path, [evidence_id] = _seed(tmp_path)

    extract.run(db_path=db_path, client=FakeClient([RuntimeError("529 overloaded")]))

    conn = db.connect(db_path)
    state = db.get_extraction_state(conn, evidence_id)
    assert (state["status"], state["attempts"]) == ("retryable", 0)
    conn.close()

    extract.run(db_path=db_path, client=FakeClient([VALID]))
    conn = db.connect(db_path)
    assert db.get_extraction_state(conn, evidence_id)["status"] == "candidate"


def test_run_stops_after_consecutive_api_errors_without_burning_attempts(tmp_path):
    db_path, ids = _seed(tmp_path, n=extract.MAX_CONSECUTIVE_API_ERRORS + 2)
    errors = [RuntimeError("down")] * extract.MAX_CONSECUTIVE_API_ERRORS

    with pytest.raises(extract.ExtractionAPIError, match="consecutive API errors"):
        extract.run(db_path=db_path, client=FakeClient(errors))

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM evidence_extraction WHERE status = 'dead'").fetchone()[0] == 0
    assert len(db.get_evidence_pending_extraction(conn)) == len(ids)


def test_crash_keeps_completed_rows_and_retries_only_the_interrupted_one(tmp_path):
    db_path, ids = _seed(tmp_path, n=3)
    crashing = FakeClient([])
    crashing.messages = CrashOnCall([VALID], crash_on=2)

    with pytest.raises(SimulatedCrash):
        extract.run(db_path=db_path, client=crashing)

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM signal_candidate").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM llm_call WHERE status = 'pending'").fetchone()[0] == 1
    conn.close()

    extract.run(db_path=db_path, client=FakeClient([VALID, VALID]))

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM signal_candidate").fetchone()[0] == 3
    statuses = [r[0] for r in conn.execute("SELECT status FROM llm_call ORDER BY call_id")]
    assert statuses == ["succeeded", "abandoned", "succeeded", "succeeded"]


def test_duplicate_evidence_is_not_extracted(tmp_path):
    db_path, [first, second] = _seed(tmp_path, n=2)
    conn = db.connect(db_path)
    conn.execute("UPDATE evidence SET duplicate_of = ? WHERE evidence_id = ?", (first, second))
    conn.commit()
    conn.close()

    extract.run(db_path=db_path, client=FakeClient([VALID]))

    conn = db.connect(db_path)
    assert [r[0] for r in conn.execute("SELECT evidence_id FROM signal_candidate")] == [first]


def test_migration_backfills_outcomes_for_extracted_evidence(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(db.SCHEMA_SQL)
    for evidence_id in ("EV-2026-000001", "EV-2026-000002"):
        raw.execute(
            "INSERT INTO evidence (evidence_id, source_type, source_name, source_url, captured_at, published_at, "
            "title, content, metadata) VALUES (?, 'reddit_post', 'sub', ?, '2026-08-15T00:00:00+00:00', "
            "'2026-08-14T00:00:00+00:00', 't', 'c', '{}')",
            (evidence_id, f"/{evidence_id}"),
        )
    raw.execute(
        "INSERT INTO signal_candidate (candidate_id, evidence_id, signal_type, summary, confidence) "
        "VALUES ('SC-2026-00001', 'EV-2026-000001', 'new_feature_demand', 's', 0.9)"
    )
    raw.commit()
    raw.close()

    conn = db.connect(str(path))
    db.init_db(conn)

    assert db.get_extraction_state(conn, "EV-2026-000001")["status"] == "candidate"
    assert db.get_extraction_state(conn, "EV-2026-000001")["decided_by"] == "backfill"
    assert [e["evidence_id"] for e in db.get_evidence_pending_extraction(conn)] == ["EV-2026-000002"]


@pytest.mark.parametrize("confidence", ["high", 1.7, -0.1, True, None, float("inf")])
def test_invalid_confidence_is_a_response_error(confidence):
    raw = json.dumps({"signal_type": "new_feature_demand", "summary": "s", "confidence": confidence})
    with pytest.raises(extract.ExtractionResponseError, match="invalid confidence"):
        extract.parse_extraction_text(raw, "x")


def test_integer_confidence_is_returned_as_float():
    raw = json.dumps({"signal_type": "new_feature_demand", "summary": "s", "confidence": 1})
    assert extract.parse_extraction_text(raw, "x")["confidence"] == 1.0


@pytest.mark.parametrize("summary", ["", "   ", 42, None, ["a"]])
def test_empty_or_non_string_summary_is_a_response_error(summary):
    raw = json.dumps({"signal_type": "new_feature_demand", "summary": summary, "confidence": 0.5})
    with pytest.raises(extract.ExtractionResponseError, match="invalid summary"):
        extract.parse_extraction_text(raw, "x")


@pytest.mark.parametrize("skip", ["no", 1, "false", [True]])
def test_only_literal_true_means_skip(skip):
    raw = json.dumps({"skip": skip, "signal_type": "new_feature_demand", "summary": "s", "confidence": 0.5})
    assert extract.parse_extraction_text(raw, "x")["signal_type"] == "new_feature_demand"


@pytest.mark.parametrize("value", ["next spring", "2027-13-01", 20270531, ""])
def test_unusable_effective_date_becomes_none(value):
    raw = json.dumps({"signal_type": "new_feature_demand", "summary": "s", "confidence": 0.5, "effective_date": value})
    assert extract.parse_extraction_text(raw, "x")["effective_date"] is None


class CapturingMessages:
    def __init__(self, text):
        self.text, self.kwargs = text, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse(self.text)


def test_request_extraction_sends_effort_only_when_configured(monkeypatch):
    client = FakeClient([])
    client.messages = CapturingMessages(VALID)

    extract.request_extraction(client, SAMPLE_EVIDENCE)
    assert "output_config" not in client.messages.kwargs

    monkeypatch.setattr(extract, "EXTRACTION_EFFORT", "low")
    extract.request_extraction(client, SAMPLE_EVIDENCE)
    assert client.messages.kwargs["output_config"] == {"effort": "low"}


TRIAGE_SKIP = json.dumps({"route": "skip", "confidence": 0.97, "reason": "spam"})
TRIAGE_EXTRACT = json.dumps({"route": "extract", "confidence": 0.8, "reason": "feedback"})


class ByModel:
    """Answers triage (Haiku) and extraction (Sonnet) calls from separate queues."""

    def __init__(self, triage_answers, extraction_answers):
        self.triage, self.extraction = list(triage_answers), list(extraction_answers)
        self.models = []

    def create(self, **kwargs):
        self.models.append(kwargs["model"])
        queue = self.triage if kwargs["model"] == triage_module.TRIAGE_MODEL else self.extraction
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


def by_model(triage_answers, extraction_answers):
    client = FakeClient([])
    client.messages = ByModel(triage_answers, extraction_answers)
    return client


def test_confident_triage_skip_never_reaches_the_extractor(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "in_audit_sample", lambda evidence_id, rate=0.05: False)
    db_path, [evidence_id] = _seed(tmp_path)
    client = by_model([TRIAGE_SKIP], [])

    extract.run(db_path=db_path, client=client, cascade=True)

    conn = db.connect(db_path)
    state = db.get_extraction_state(conn, evidence_id)
    assert (state["status"], state["decided_by"]) == ("skipped", "triage")
    assert client.messages.models == [triage_module.TRIAGE_MODEL]
    call = ledger.get_call(conn, state["last_call_id"])
    assert (call["stage"], call["parsed"]["route"], call["request_meta"]["audit"]) == ("triage", "skip", False)


def test_triage_extract_route_goes_on_to_the_extractor(tmp_path):
    db_path, [evidence_id] = _seed(tmp_path)

    extract.run(db_path=db_path, client=by_model([TRIAGE_EXTRACT], [VALID]), cascade=True)

    conn = db.connect(db_path)
    assert db.get_extraction_state(conn, evidence_id)["decided_by"] == "extractor"


@pytest.mark.parametrize("triage_answer", [RuntimeError("haiku down"), "not json",
                                           json.dumps({"route": "skip", "confidence": 0.5})])
def test_triage_failure_falls_through_to_extraction(tmp_path, triage_answer):
    db_path, [evidence_id] = _seed(tmp_path)

    extract.run(db_path=db_path, client=by_model([triage_answer], [VALID]), cascade=True)

    conn = db.connect(db_path)
    assert db.get_extraction_state(conn, evidence_id)["status"] == "candidate"


def test_audited_triage_skip_is_still_extracted(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "in_audit_sample", lambda evidence_id, rate=0.05: True)
    db_path, [evidence_id] = _seed(tmp_path)

    extract.run(db_path=db_path, client=by_model([TRIAGE_SKIP], [VALID]), cascade=True)

    conn = db.connect(db_path)
    assert db.get_extraction_state(conn, evidence_id)["status"] == "candidate"
    meta = conn.execute("SELECT request_meta FROM llm_call WHERE stage = 'triage'").fetchone()[0]
    assert json.loads(meta)["audit"] is True


def test_cascade_off_makes_no_triage_call(tmp_path):
    db_path, _ = _seed(tmp_path)
    client = by_model([], [VALID])

    extract.run(db_path=db_path, client=client)

    assert client.messages.models == [extract.EXTRACTION_MODEL]


def test_audit_sample_is_deterministic_and_close_to_the_rate():
    ids = [f"EV-2026-{i:06d}" for i in range(4000)]
    sampled = [i for i in ids if extract.in_audit_sample(i)]
    assert sampled == [i for i in ids if extract.in_audit_sample(i)]
    assert 0.03 < len(sampled) / len(ids) < 0.07
