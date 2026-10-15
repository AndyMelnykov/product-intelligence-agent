"""The LLM call ledger: one row per model call, written before the call and completed after it.

Every stage that calls a model follows the same three steps (the "effect sandwich"):

1. start_call() records the intent and COMMITS, so a crash during the call leaves a visible
   'pending' row instead of nothing.
2. The caller makes the API call.
3. finish_call() records the outcome. The caller commits it in the same transaction as the rows
   derived from it, so a result and its effects land together or not at all.

The Anthropic API has no idempotency key, so a call interrupted mid-flight cannot be resumed.
The next run marks it 'abandoned' and makes the call again: a crash costs at most one repeated
call per stage, not the whole run. This assumes one pipeline process at a time.
"""
import json

import db
from fingerprints import fingerprint

STATUSES = ("pending", "succeeded", "api_error", "response_error", "abandoned")
_TERMINAL = ("succeeded", "api_error", "response_error")


def start_call(conn, *, stage, subject_key, model, prompt_template, request_meta=None):
    cursor = conn.execute(
        "INSERT INTO llm_call (stage, subject_key, model, prompt_fingerprint, request_meta, status, started_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (stage, subject_key, model, fingerprint(prompt_template), _dump(request_meta), db.utc_now()),
    )
    conn.commit()
    return cursor.lastrowid


def finish_call(conn, call_id, *, status, reply=None, parsed=None, error=None):
    if status not in _TERMINAL:
        raise ValueError(f"finish_call status must be one of {_TERMINAL}, got {status!r}")
    conn.execute(
        "UPDATE llm_call SET status = ?, raw_response = ?, parsed = ?, error = ?, stop_reason = ?, "
        "input_tokens = ?, output_tokens = ?, finished_at = ? WHERE call_id = ?",
        (
            status,
            reply.text if reply else None,
            _dump(parsed),
            error,
            reply.stop_reason if reply else None,
            reply.input_tokens if reply else None,
            reply.output_tokens if reply else None,
            db.utc_now(),
            call_id,
        ),
    )


def abandon_stale_calls(conn, stage):
    cursor = conn.execute(
        "UPDATE llm_call SET status = 'abandoned', finished_at = ? WHERE stage = ? AND status = 'pending'",
        (db.utc_now(), stage),
    )
    conn.commit()
    return cursor.rowcount


def mark_applied(conn, call_id):
    conn.execute("UPDATE llm_call SET applied_at = ? WHERE call_id = ?", (db.utc_now(), call_id))


def get_call(conn, call_id):
    row = conn.execute("SELECT * FROM llm_call WHERE call_id = ?", (call_id,)).fetchone()
    return _row_to_dict(row) if row else None


def get_unapplied_calls(conn, stage):
    rows = conn.execute(
        "SELECT * FROM llm_call WHERE stage = ? AND status = 'succeeded' AND applied_at IS NULL ORDER BY call_id",
        (stage,),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _dump(value):
    return json.dumps(value, sort_keys=True) if value is not None else None


def _row_to_dict(row):
    d = dict(row)
    for key in ("request_meta", "parsed"):
        d[key] = json.loads(d[key]) if d[key] is not None else None
    return d
