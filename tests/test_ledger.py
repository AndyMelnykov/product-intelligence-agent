import pytest

import db
import ledger
from fingerprints import fingerprint
from llm import LLMReply


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "pi_agent.db"
    conn = db.connect(str(path))
    db.init_db(conn)
    conn.close()
    return str(path)


def test_start_call_commits_a_pending_row_visible_to_other_connections(db_path):
    conn = db.connect(db_path)
    call_id = ledger.start_call(conn, stage="extract", subject_key="EV-2026-000001", model="m",
                                prompt_template="TEMPLATE", request_meta={"k": 1})

    other = db.connect(db_path)
    row = ledger.get_call(other, call_id)
    assert row["status"] == "pending"
    assert row["prompt_fingerprint"] == fingerprint("TEMPLATE")
    assert row["request_meta"] == {"k": 1}
    assert row["parsed"] is None


def test_finish_call_records_reply_fields(db_path):
    conn = db.connect(db_path)
    call_id = ledger.start_call(conn, stage="extract", subject_key="s", model="m", prompt_template="T")
    reply = LLMReply(text='{"a": 1}', stop_reason="end_turn", input_tokens=10, output_tokens=5)

    ledger.finish_call(conn, call_id, status="succeeded", reply=reply, parsed={"a": 1})
    conn.commit()

    row = ledger.get_call(conn, call_id)
    assert (row["status"], row["raw_response"], row["parsed"]) == ("succeeded", '{"a": 1}', {"a": 1})
    assert (row["input_tokens"], row["output_tokens"], row["stop_reason"]) == (10, 5, "end_turn")
    assert row["finished_at"] is not None


def test_finish_call_without_commit_is_not_visible_elsewhere(db_path):
    conn = db.connect(db_path)
    call_id = ledger.start_call(conn, stage="extract", subject_key="s", model="m", prompt_template="T")
    ledger.finish_call(conn, call_id, status="api_error", error="boom")

    other = db.connect(db_path)
    assert ledger.get_call(other, call_id)["status"] == "pending"


@pytest.mark.parametrize("status", ["pending", "abandoned", "weird"])
def test_finish_call_rejects_non_terminal_statuses(db_path, status):
    conn = db.connect(db_path)
    call_id = ledger.start_call(conn, stage="extract", subject_key="s", model="m", prompt_template="T")
    with pytest.raises(ValueError):
        ledger.finish_call(conn, call_id, status=status)


def test_abandon_stale_calls_only_touches_pending_rows_of_that_stage(db_path):
    conn = db.connect(db_path)
    stale = ledger.start_call(conn, stage="extract", subject_key="a", model="m", prompt_template="T")
    other_stage = ledger.start_call(conn, stage="match", subject_key="b", model="m", prompt_template="T")
    done = ledger.start_call(conn, stage="extract", subject_key="c", model="m", prompt_template="T")
    ledger.finish_call(conn, done, status="succeeded", parsed={"x": 1})
    conn.commit()

    assert ledger.abandon_stale_calls(conn, "extract") == 1

    assert ledger.get_call(conn, stale)["status"] == "abandoned"
    assert ledger.get_call(conn, other_stage)["status"] == "pending"
    assert ledger.get_call(conn, done)["status"] == "succeeded"


def test_get_unapplied_calls_lists_succeeded_calls_until_marked_applied(db_path):
    conn = db.connect(db_path)
    first = ledger.start_call(conn, stage="match", subject_key="w", model="m", prompt_template="T",
                              request_meta={"candidate_ids": ["SC-2026-00001"]})
    ledger.finish_call(conn, first, status="succeeded", parsed=[{"index": 0}])
    failed = ledger.start_call(conn, stage="match", subject_key="w", model="m", prompt_template="T")
    ledger.finish_call(conn, failed, status="response_error", error="bad")
    conn.commit()

    unapplied = ledger.get_unapplied_calls(conn, "match")
    assert [c["call_id"] for c in unapplied] == [first]
    assert unapplied[0]["parsed"] == [{"index": 0}]
    assert unapplied[0]["request_meta"] == {"candidate_ids": ["SC-2026-00001"]}

    ledger.mark_applied(conn, first)
    conn.commit()
    assert ledger.get_unapplied_calls(conn, "match") == []
