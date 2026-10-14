# P1: Durable Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every pipeline stage safe to re-run after a crash at any point: no lost paid LLM work, no duplicates, no events lost or emitted twice. Make every malformed model output harmless.

**Architecture:**
- A numbered migration framework in `db.py` evolves the schema of existing databases in place.
- An LLM call ledger (`llm_call`, `ledger.py`) records every model call before it is made (intent, committed) and after it returns (result, committed together with its derived rows). This is the "effect sandwich".
- Fetch becomes idempotent through a natural key, and its cursor moves into SQLite.
- Extraction records skips and failures as outcomes.
- Matching stores its response before applying it, then applies decisions one candidate at a time through a pure resolution step.
- Materiality writes events through an outbox.

**Tech Stack:** Python 3.11, sqlite3 (stdlib), Anthropic Python SDK 0.122, PRAW, pytest, ruff.

**Spec:** [docs/superpowers/specs/2026-10-14-durability-and-quality-roadmap-design.md](../specs/2026-10-14-durability-and-quality-roadmap-design.md). This plan covers sections 1.1, 1.2 (S1-S3), 1.3 (P1, P2) and 1.7, and the P1 decisions in section 5. Section 4 lists the names later plans depend on, so keep them exact.

## Global Constraints

- Python 3.11 syntax only (CI runs 3.11).
- `ruff check .` must pass (rules E, F, W; E501 ignored).
- No pytest test makes a real API call. Every test injects a fake client.
- Every file read and write passes `encoding="utf-8"`.
- `SCHEMA_SQL` in `db.py` is frozen from Task 2 on. Every schema change is a numbered entry in `db.MIGRATIONS`. Numbers 1-5 belong to this plan (Tasks 3, 4, 5, 6, 8).
- One pipeline process at a time (unchanged assumption). `ledger.abandon_stale_calls` relies on it.
- Model IDs stay `claude-sonnet-5` (extraction, matching) and `claude-opus-5` (judge).
- Existing public names keep working: `extract.extract_topic`, `match.call_matcher`, `match.apply_matches`, `db.get_evidence_without_candidate`, `materiality.emit_event`, `runs.fingerprint`.
- The event envelope format (`event_version: "1.0"`) does not change.

## Review Focus

1. **Upgrading a real, existing `data/pi_agent.db`.** Expected: the first run after this plan migrates the database in place, keeps every row, and marks duplicate Reddit posts instead of failing on the new unique index. Pinned in Task 4 (`test_migration_marks_existing_duplicates`) and Task 5 (`test_migration_backfills_outcomes_for_extracted_evidence`).
2. **The API being down for the whole run.** Expected: extraction stops after 5 consecutive API errors with a clear error, marks nothing `dead`, and the next healthy run picks up every row. Pinned in Task 5 (`test_run_stops_after_consecutive_api_errors_without_burning_attempts`).
3. **The model returning the same new topic for two candidates in one batch.** Expected: one topic is created and both candidates point to it; the week does not roll back. Pinned in Task 6 (`test_apply_matches_groups_candidates_proposing_the_same_new_slug`).
4. **A crash after the matching response is paid for but before it is applied.** Expected: the next run applies the stored response without calling the API again. Pinned in Task 7 (`test_run_replays_stored_response_after_crash_without_calling_api`).
5. **Re-running materiality in the same week, or crashing between commit and event write.** Expected: no duplicate signals or events, and the missing event is written on the next run. Pinned in Task 8.

---

## File Structure

| File | Responsibility |
|---|---|
| `CLAUDE.md` (create) | Agent guidance: which layers humans design, rules, commands |
| `.github/pull_request_template.md` (create) | PR checklist, including "could this have removed code?" |
| `fingerprints.py` (create) | `fingerprint()` shared by production and evals |
| `llm.py` (create) | `LLMReply`, `reply_from_response()`: one way to read a model response |
| `ledger.py` (create) | LLM call ledger: start/finish/abandon/mark applied/read |
| `db.py` (modify) | Migration framework, migrations 1-5, new helpers |
| `fetch.py` (modify) | Idempotent inserts, cursor in SQLite, per-subreddit commits |
| `extract.py` (modify) | Split request/parse, per-row effect sandwich, outcomes |
| `match.py` (modify) | Pure resolution, safe apply, stored responses, replay, narrow retry |
| `materiality.py` (modify) | One signal per topic and week, event outbox |
| `query.py` (modify) | `health`, `calls`, `usage` commands |
| `evals/runs.py` (modify) | Import `fingerprint` from `fingerprints.py` |
| `docs/decisions/005-per-item-durable-llm-calls.md` (create) | ADR for this plan |
| `README.md` (modify) | Architecture, Observability, Limitations |
| `tests/test_db_migrations.py`, `tests/test_ledger.py`, `tests/test_llm.py`, `tests/test_match_resolution.py`, `tests/test_resume.py` (create) | New unit and end-to-end tests |
| `tests/test_fetch.py`, `tests/test_extract.py`, `tests/test_match.py`, `tests/test_materiality.py`, `tests/test_query.py` (modify) | Updated and new tests |

**Branching:** from an up-to-date `main` (after the evals branch merges): `git checkout main && git pull && git checkout -b feat/durable-pipeline`.

---

### Task 1: Agent guidance and PR template

**Files:**
- Create: `CLAUDE.md`
- Create: `.github/pull_request_template.md`

**Interfaces:**
- Consumes: nothing.
- Produces: `CLAUDE.md`, which Tasks 2 and 9 extend by one line each.

- [ ] **Step 1: Write `CLAUDE.md`**

```markdown
# CLAUDE.md

How this repo is run, for coding agents and for humans who want the short version.

## Commands

- Tests: `pytest`. No network or credentials; every LLM call in tests uses a fake client.
- Lint: `ruff check .`
- Pipeline: `python run_weekly.py [--stage fetch|extract|match|report|materiality]`
- Evals call the real API, cost money and never run in CI. See README "Evals".

## Who designs what

A human designs these. Change them only when asked to, and call the change out in the PR:

- The schema in `db.py`: `SCHEMA_SQL` and `MIGRATIONS`. Data outlives code, and a bad migration corrupts history.
- LLM prompt and response contracts: every `*_PROMPT_TEMPLATE`, the parsers, `SIGNAL_TYPES`.
- The `materiality.py` rule table and thresholds, and the trend math in `report.py` (ADR 003).
- What each stage commits, and when.
- Golden labels in `evals/golden/`. A human decides what is correct.

Agent-written code is fine here if tests pass:

- `query.py` output formatting, report and CSV layout, eval console output.
- Test scaffolding and fixtures.
- Docs that describe existing behavior.

## Rules

- Read `docs/decisions/` before changing architecture. If a change contradicts an ADR, stop and ask.
- Fix causes, not symptoms. Before adding code to fix a bug, check whether the fix can remove or simplify code instead. Say which you did in the PR.
- No real API calls in pytest. Inject fake clients the way `tests/test_extract.py` does.
- Pass `encoding="utf-8"` to every file read and write.
- Python 3.11 (CI). No 3.12+ syntax.
- A prompt change changes the eval fingerprints. Run the affected eval and compare runs (README "Run history and drift").
```

- [ ] **Step 2: Write `.github/pull_request_template.md`**

```markdown
## What and why

## Net code change

- Lines added / removed:
- Could this fix have removed or simplified code instead of adding it? What did you choose, and why?

## Checks

- [ ] `pytest` and `ruff check .` pass
- [ ] Touches a human-designed area (see CLAUDE.md)? Called out above.
- [ ] Prompt changed? Eval run and comparison pasted (`python evals/compare_runs.py --kind ...`).
- [ ] Schema changed? New numbered migration, tested against a copy of an existing database.
```

- [ ] **Step 3: Verify nothing else changed**

Run: `pytest -q && ruff check .`
Expected: the same pass count as before the task; ruff clean.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md .github/pull_request_template.md
git commit -m "docs: agent guidance on design ownership and a PR template"
```

---

### Task 2: Shared fingerprints and the migration framework

**Files:**
- Create: `fingerprints.py`
- Modify: `evals/runs.py` (remove `fingerprint` and the `hashlib` import; import it instead)
- Modify: `db.py` (imports, `init_db`, new framework functions)
- Modify: `CLAUDE.md` (one rule)
- Test: `tests/test_db_migrations.py` (create)

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `fingerprints.fingerprint(value) -> str`
  - `db.MIGRATIONS: list[tuple[int, Callable]]` (empty in this task)
  - `db.init_db(conn)`, `db.apply_migrations(conn, migrations=None)`, `db.schema_version(conn) -> int`
  - `db._add_column(conn, table, column_sql)`, `db._column_names(conn, table) -> set[str]`
  - `db.utc_now() -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_db_migrations.py`:

```python
import sqlite3

import pytest

import db
from fingerprints import fingerprint


def legacy_connection(path):
    """A database exactly as the pre-migration code created it: SCHEMA_SQL only, user_version 0."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(db.SCHEMA_SQL)
    conn.commit()
    return conn


def test_fingerprint_is_stable_for_strings_and_json_values():
    assert fingerprint("abc") == fingerprint("abc")
    assert len(fingerprint("abc")) == 12
    assert fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})


def test_runs_module_still_exposes_fingerprint():
    from evals import runs
    assert runs.fingerprint("abc") == fingerprint("abc")


def test_init_db_brings_a_fresh_database_to_the_latest_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    latest = max((version for version, _ in db.MIGRATIONS), default=0)
    assert db.schema_version(conn) == latest


def test_apply_migrations_runs_each_pending_migration_once_in_order():
    conn = db.connect(":memory:")
    db.init_db(conn)
    calls = []
    base = db.schema_version(conn)
    migrations = [
        (base + 1, lambda c: calls.append(base + 1)),
        (base + 2, lambda c: calls.append(base + 2)),
    ]

    db.apply_migrations(conn, migrations)
    db.apply_migrations(conn, migrations)

    assert calls == [base + 1, base + 2]
    assert db.schema_version(conn) == base + 2


def test_failed_migration_rolls_back_its_changes_and_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    base = db.schema_version(conn)

    def broken(c):
        c.execute("CREATE TABLE half_done (x INTEGER)")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        db.apply_migrations(conn, [(base + 1, broken)])

    assert db.schema_version(conn) == base
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half_done" not in tables


def test_init_db_upgrades_a_legacy_database_and_keeps_its_rows(tmp_path):
    path = tmp_path / "legacy.db"
    conn = legacy_connection(path)
    conn.execute(
        "INSERT INTO evidence (evidence_id, source_type, source_name, source_url, captured_at, published_at, "
        "title, content, metadata) VALUES ('EV-2026-000001', 'reddit_post', 'sub', '/a', "
        "'2026-08-15T00:00:00+00:00', '2026-08-14T00:00:00+00:00', 't', 'c', '{\"reddit_post_id\": \"a1\"}')"
    )
    conn.commit()
    conn.close()

    conn = db.connect(str(path))
    db.init_db(conn)

    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
    assert db.schema_version(conn) == max((v for v, _ in db.MIGRATIONS), default=0)


def test_add_column_is_idempotent():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db._add_column(conn, "evidence", "scratch TEXT")
    db._add_column(conn, "evidence", "scratch TEXT")
    assert "scratch" in db._column_names(conn, "evidence")


def test_utc_now_is_iso_utc_to_the_second():
    stamp = db.utc_now()
    assert stamp.endswith("+00:00")
    assert len(stamp) == len("2026-10-14T12:00:00+00:00")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_db_migrations.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'fingerprints'`.

- [ ] **Step 3: Create `fingerprints.py`**

```python
"""Short, stable content fingerprints for prompts, golden data, and registry snapshots."""
import hashlib
import json


def fingerprint(value):
    """First 12 hex chars of a SHA-256 over a string, or over any JSON-serializable value."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]
```

- [ ] **Step 4: Make `evals/runs.py` import it**

In `evals/runs.py`, delete `import hashlib` and the whole `def fingerprint(value):` function. Add this import right after the existing stdlib imports:

```python
from fingerprints import fingerprint
```

`golden_fingerprint` keeps calling `fingerprint(...)`, so the name stays used and `runs.fingerprint` still resolves.

- [ ] **Step 5: Add the migration framework to `db.py`**

Add `from datetime import datetime, timezone` to the imports at the top of `db.py`. Replace the existing `init_db` with:

```python
# Schema changes after the v0 baseline above. Each entry is (version, function); the function
# runs inside one transaction together with the PRAGMA user_version bump. Never edit SCHEMA_SQL
# or renumber an entry that has shipped: append a new migration instead.
MIGRATIONS = []


def init_db(conn):
    conn.executescript(SCHEMA_SQL)
    apply_migrations(conn)


def schema_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def apply_migrations(conn, migrations=None):
    migrations = MIGRATIONS if migrations is None else migrations
    current = schema_version(conn)
    for version, migrate in migrations:
        if version <= current:
            continue
        conn.execute("BEGIN")
        try:
            migrate(conn)
            conn.execute(f"PRAGMA user_version = {int(version)}")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        current = version


def _column_names(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table, column_sql):
    name = column_sql.split()[0]
    if name not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
```

`executescript` commits any open transaction before it runs, so `apply_migrations` always starts outside a transaction, and the explicit `BEGIN` is safe.

- [ ] **Step 6: Add the migration rule to `CLAUDE.md`**

Append to the `## Rules` list:

```markdown
- Schema changes are a new numbered entry in `db.MIGRATIONS`. Never edit `SCHEMA_SQL` and never renumber a shipped migration.
```

- [ ] **Step 7: Run all tests**

Run: `pytest -q && ruff check .`
Expected: all pass, including the 8 new tests.

- [ ] **Step 8: Commit**

```bash
git add fingerprints.py evals/runs.py db.py CLAUDE.md tests/test_db_migrations.py
git commit -m "feat(db): numbered schema migrations and a shared fingerprint module"
```

---

### Task 3: The LLM call ledger

**Files:**
- Create: `llm.py`
- Create: `ledger.py`
- Modify: `db.py` (migration 1)
- Test: `tests/test_llm.py`, `tests/test_ledger.py` (create)

**Interfaces:**
- Consumes: `db.MIGRATIONS`, `db.utc_now()`, `fingerprints.fingerprint()` (Task 2).
- Produces:
  - `llm.LLMReply(text, stop_reason, input_tokens, output_tokens)` (frozen dataclass)
  - `llm.reply_from_response(response) -> LLMReply`
  - `ledger.STATUSES`
  - `ledger.start_call(conn, *, stage, subject_key, model, prompt_template, request_meta=None) -> int` (commits)
  - `ledger.finish_call(conn, call_id, *, status, reply=None, parsed=None, error=None)` (no commit)
  - `ledger.abandon_stale_calls(conn, stage) -> int` (commits)
  - `ledger.mark_applied(conn, call_id)` (no commit)
  - `ledger.get_call(conn, call_id) -> dict | None`
  - `ledger.get_unapplied_calls(conn, stage) -> list[dict]`
  - Table `llm_call` (spec §4.4)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_llm.py`:

```python
from llm import LLMReply, reply_from_response


class Block:
    def __init__(self, type, text=None):
        self.type = type
        self.text = text


class Usage:
    input_tokens = 120
    output_tokens = 30


class Response:
    def __init__(self, content, stop_reason="end_turn", usage=None):
        self.content = content
        self.stop_reason = stop_reason
        if usage is not None:
            self.usage = usage


def test_reply_reads_first_text_block_after_thinking():
    reply = reply_from_response(Response([Block("thinking"), Block("text", "hello"), Block("text", "later")], usage=Usage()))
    assert reply == LLMReply(text="hello", stop_reason="end_turn", input_tokens=120, output_tokens=30)


def test_reply_without_text_or_usage_has_nones():
    reply = reply_from_response(Response([Block("thinking")], stop_reason="max_tokens"))
    assert reply == LLMReply(text=None, stop_reason="max_tokens", input_tokens=None, output_tokens=None)
```

Create `tests/test_ledger.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_llm.py tests/test_ledger.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'llm'`.

- [ ] **Step 3: Create `llm.py`**

```python
"""One way to read an Anthropic Messages API response, shared by every model-calling stage."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMReply:
    text: str | None
    stop_reason: str | None
    input_tokens: int | None
    output_tokens: int | None


def reply_from_response(response):
    # Sonnet 5 and Opus 5 run adaptive thinking by default, so a thinking block may precede the answer.
    text = next((block.text for block in response.content if getattr(block, "type", None) == "text"), None)
    usage = getattr(response, "usage", None)
    return LLMReply(
        text=text,
        stop_reason=getattr(response, "stop_reason", None),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
```

- [ ] **Step 4: Add migration 1 to `db.py`**

Add this function above the `MIGRATIONS` list, and register it:

```python
def _migration_1_llm_call(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS llm_call (
          call_id INTEGER PRIMARY KEY AUTOINCREMENT,
          stage TEXT NOT NULL,
          subject_key TEXT NOT NULL,
          model TEXT NOT NULL,
          prompt_fingerprint TEXT NOT NULL,
          request_meta TEXT,
          status TEXT NOT NULL,
          raw_response TEXT,
          parsed TEXT,
          error TEXT,
          stop_reason TEXT,
          input_tokens INTEGER,
          output_tokens INTEGER,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          applied_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_call_stage_status ON llm_call(stage, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_call_subject ON llm_call(stage, subject_key)")


MIGRATIONS = [
    (1, _migration_1_llm_call),
]
```

(Replace the empty `MIGRATIONS = []` from Task 2 with this list, keeping the comment above it.)

- [ ] **Step 5: Create `ledger.py`**

```python
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
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_llm.py tests/test_ledger.py tests/test_db_migrations.py -v`
Expected: PASS.

- [ ] **Step 7: Run everything and commit**

Run: `pytest -q && ruff check .`
Expected: all pass.

```bash
git add llm.py ledger.py db.py tests/test_llm.py tests/test_ledger.py
git commit -m "feat: LLM call ledger recording intent before and outcome after every model call"
```

---

### Task 4: Idempotent fetch with the cursor in SQLite

**Files:**
- Modify: `db.py` (migration 2; `insert_evidence` gains `source_key`; `insert_evidence_if_new`; cursor helpers)
- Modify: `fetch.py` (`run`)
- Test: `tests/test_fetch.py` (replace the two `test_run_*` tests; add new ones)

**Interfaces:**
- Consumes: `db.MIGRATIONS`, `db._add_column`, `db.utc_now` (Task 2).
- Produces:
  - Columns `evidence.source_key TEXT` (partial unique index `idx_evidence_source_key`) and `evidence.duplicate_of TEXT`
  - Table `fetch_cursor(source_type, source_name, cursor, updated_at, PRIMARY KEY(source_type, source_name))`
  - `db.insert_evidence(conn, *, ..., source_key=None) -> str` (the new keyword is optional)
  - `db.insert_evidence_if_new(conn, *, source_key, **fields) -> str | None`
  - `db.get_fetch_cursor(conn, source_type, source_name) -> str | None`
  - `db.set_fetch_cursor(conn, source_type, source_name, cursor)`
  - `fetch.source_key_for(post) -> str` (`"reddit_post:<id>"`)

- [ ] **Step 1: Write the failing tests**

In `tests/test_fetch.py`, **delete** `test_run_inserts_evidence_rows_and_updates_state` and `test_run_leaves_state_and_db_untouched_when_a_subreddit_fetch_fails`, then append:

```python
import sqlite3


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_fetch.py -v`
Expected: the new tests FAIL (`no such column: source_key`, `AttributeError: ... get_fetch_cursor`).

- [ ] **Step 3: Add migration 2 to `db.py`**

```python
def _migration_2_evidence_identity(conn):
    _add_column(conn, "evidence", "source_key TEXT")
    _add_column(conn, "evidence", "duplicate_of TEXT REFERENCES evidence(evidence_id)")

    # Backfill keys for rows fetched before keys existed. The first row per Reddit post keeps the
    # key; later copies of the same post are marked duplicate_of the first and keep no key.
    first_by_key = {}
    for row in conn.execute("SELECT evidence_id, source_type, metadata FROM evidence ORDER BY evidence_id").fetchall():
        post_id = json.loads(row["metadata"]).get("reddit_post_id")
        if row["source_type"] != "reddit_post" or not post_id:
            continue
        key = f"reddit_post:{post_id}"
        if key in first_by_key:
            conn.execute("UPDATE evidence SET duplicate_of = ? WHERE evidence_id = ?", (first_by_key[key], row["evidence_id"]))
        else:
            first_by_key[key] = row["evidence_id"]
            conn.execute("UPDATE evidence SET source_key = ? WHERE evidence_id = ?", (key, row["evidence_id"]))

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_source_key ON evidence(source_key) WHERE source_key IS NOT NULL"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fetch_cursor (
          source_type TEXT NOT NULL,
          source_name TEXT NOT NULL,
          cursor TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (source_type, source_name)
        )"""
    )
```

Register it: `MIGRATIONS = [(1, _migration_1_llm_call), (2, _migration_2_evidence_identity)]`.

`connect()` sets `row_factory = sqlite3.Row`, so `row["metadata"]` works inside migrations. The legacy test opens the file with plain `sqlite3`, but `init_db` runs on a connection from `db.connect`.

- [ ] **Step 4: Update `insert_evidence` and add the new helpers in `db.py`**

Replace `insert_evidence` with:

```python
def insert_evidence(conn, *, source_type, source_name, source_url, captured_at,
                     published_at, title, content, metadata, source_key=None):
    year = captured_at[:4]
    evidence_id = _next_sequence_id(conn, "evidence", "evidence_id", f"EV-{year}-", 6)
    conn.execute(
        "INSERT INTO evidence (evidence_id, source_type, source_name, source_url, "
        "captured_at, published_at, title, content, metadata, source_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (evidence_id, source_type, source_name, source_url, captured_at, published_at,
         title, content, json.dumps(metadata, sort_keys=True), source_key),
    )
    return evidence_id


def insert_evidence_if_new(conn, *, source_key, **fields):
    """Insert unless evidence with this source_key already exists. Returns the new id, or None."""
    if conn.execute("SELECT 1 FROM evidence WHERE source_key = ?", (source_key,)).fetchone():
        return None
    return insert_evidence(conn, source_key=source_key, **fields)


def get_fetch_cursor(conn, source_type, source_name):
    row = conn.execute(
        "SELECT cursor FROM fetch_cursor WHERE source_type = ? AND source_name = ?", (source_type, source_name)
    ).fetchone()
    return row["cursor"] if row else None


def set_fetch_cursor(conn, source_type, source_name, cursor):
    conn.execute(
        "INSERT INTO fetch_cursor (source_type, source_name, cursor, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(source_type, source_name) DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at",
        (source_type, source_name, cursor, utc_now()),
    )
```

- [ ] **Step 5: Rewrite `fetch.run`**

Replace `fetch.run` with the code below, and add `source_key_for` above it. `fetch_new_posts` and the error wrapping are unchanged.

```python
SOURCE_TYPE = "reddit_post"


def source_key_for(post):
    return f"{SOURCE_TYPE}:{post['id']}"


def run(subreddits, fetch_limit_per_subreddit, state_path="data/state.json",
        db_path="data/pi_agent.db", today=None, reddit_client=None):
    """Fetch each subreddit and commit its evidence and cursor together. A FetchError for one
    subreddit doesn't stop the others; the run raises at the end naming every failure."""
    reddit_client = reddit_client or build_reddit_client()
    # The cursor used to live in state.json. Read it only as a fallback for subreddits the
    # database has no cursor for yet; it is never written again.
    legacy_state = load_state(state_path)
    run_date = today or date.today()
    captured_at = datetime(run_date.year, run_date.month, run_date.day, tzinfo=timezone.utc).isoformat()

    conn = db.connect(db_path)
    db.init_db(conn)
    failures = []
    try:
        for subreddit_name in subreddits:
            last_seen = db.get_fetch_cursor(conn, SOURCE_TYPE, subreddit_name) or legacy_state.get(subreddit_name)
            try:
                posts, newest = fetch_new_posts(reddit_client, subreddit_name, last_seen, fetch_limit_per_subreddit)
            except FetchError as e:
                failures.append(str(e))
                continue

            for post in posts:
                db.insert_evidence_if_new(
                    conn,
                    source_key=source_key_for(post),
                    source_type=SOURCE_TYPE,
                    source_name=subreddit_name,
                    source_url=post["permalink"],
                    captured_at=captured_at,
                    published_at=datetime.fromtimestamp(post["created_utc"], tz=timezone.utc).isoformat(),
                    title=post["title"],
                    content=post["selftext"],
                    metadata={"reddit_post_id": post["id"], "score": post["score"], "num_comments": post["num_comments"]},
                )
            if newest:
                db.set_fetch_cursor(conn, SOURCE_TYPE, subreddit_name, newest)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    if failures:
        raise FetchError("; ".join(failures))
```

Change the import line `from state import load_state, save_state` to `from state import load_state`.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_fetch.py tests/test_end_to_end.py tests/test_db.py -v`
Expected: PASS. The end-to-end tests still pass because their posts have distinct ids.

- [ ] **Step 7: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add db.py fetch.py tests/test_fetch.py
git commit -m "feat(fetch): idempotent evidence inserts by Reddit post id, cursor in SQLite"
```

---

### Task 5: Durable extraction with recorded outcomes

**Files:**
- Modify: `db.py` (migration 3; `insert_signal_candidate` gains `llm_call_id`; extraction-outcome helpers)
- Modify: `extract.py` (full replacement below)
- Test: `tests/test_extract.py` (append)

**Interfaces:**
- Consumes: `ledger.*`, `llm.reply_from_response` (Task 3); `evidence.duplicate_of` (Task 4).
- Produces:
  - Table `evidence_extraction` (spec §4.5); column `signal_candidate.llm_call_id`
  - `db.insert_signal_candidate(..., llm_call_id=None)`
  - `db.get_evidence_pending_extraction(conn) -> list[dict]`
  - `db.get_extraction_state(conn, evidence_id) -> dict | None`
  - `db.record_extraction_outcome(conn, evidence_id, status, call_id, *, decided_by)` (`status` in `candidate|skipped`)
  - `db.record_extraction_failure(conn, evidence_id, call_id, *, count_attempt, max_attempts) -> str` (returns `retryable` or `dead`)
  - `extract.request_extraction(client, evidence) -> LLMReply` (raises `ExtractionAPIError`)
  - `extract.parse_extraction_reply(reply, evidence_label) -> dict | None`
  - `extract.parse_extraction_text(raw_text, evidence_label) -> dict | None`
  - `extract.extract_one(conn, client, evidence) -> str`: one of `candidate`, `skipped`, `api_error`, `response_error`
  - `extract.MAX_EXTRACTION_ATTEMPTS = 3`, `extract.MAX_CONSECUTIVE_API_ERRORS = 5`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extract.py`:

```python
import sqlite3

import ledger


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_extract.py -v`
Expected: the new tests FAIL (`no such table: evidence_extraction` or `AttributeError`).

- [ ] **Step 3: Add migration 3 and the helpers to `db.py`**

```python
def _migration_3_extraction_outcome(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS evidence_extraction (
          evidence_id TEXT PRIMARY KEY REFERENCES evidence(evidence_id),
          status TEXT NOT NULL,
          attempts INTEGER NOT NULL,
          decided_by TEXT,
          last_call_id INTEGER REFERENCES llm_call(call_id),
          updated_at TEXT NOT NULL
        )"""
    )
    _add_column(conn, "signal_candidate", "llm_call_id INTEGER REFERENCES llm_call(call_id)")
    conn.execute(
        "INSERT OR IGNORE INTO evidence_extraction (evidence_id, status, attempts, decided_by, last_call_id, updated_at) "
        "SELECT DISTINCT evidence_id, 'candidate', 0, 'backfill', NULL, ? FROM signal_candidate",
        (utc_now(),),
    )
```

Register it as `(3, _migration_3_extraction_outcome)`.

Replace `insert_signal_candidate` with a version that takes `llm_call_id`:

```python
def insert_signal_candidate(conn, *, evidence_id, signal_type, summary, confidence, topic_id=None,
                             entity=None, effective_date=None, llm_call_id=None):
    evidence = get_evidence(conn, evidence_id)
    if evidence is None:
        raise DBError(f"cannot create signal_candidate: evidence {evidence_id} does not exist")
    year = evidence["captured_at"][:4]
    candidate_id = _next_sequence_id(conn, "signal_candidate", "candidate_id", f"SC-{year}-", 5)
    conn.execute(
        "INSERT INTO signal_candidate (candidate_id, evidence_id, signal_type, topic_id, summary, "
        "confidence, entity, effective_date, llm_call_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (candidate_id, evidence_id, signal_type, topic_id, summary, confidence,
         json.dumps(entity, sort_keys=True) if entity is not None else None, effective_date, llm_call_id),
    )
    return candidate_id
```

Add the outcome helpers:

```python
def get_evidence_pending_extraction(conn):
    """Evidence that is not a duplicate and has either no extraction outcome yet or a retryable one."""
    rows = conn.execute(
        "SELECT e.* FROM evidence e LEFT JOIN evidence_extraction x ON x.evidence_id = e.evidence_id "
        "WHERE e.duplicate_of IS NULL AND (x.evidence_id IS NULL OR x.status = 'retryable') "
        "ORDER BY e.evidence_id"
    ).fetchall()
    return [_evidence_row_to_dict(r) for r in rows]


def get_extraction_state(conn, evidence_id):
    row = conn.execute("SELECT * FROM evidence_extraction WHERE evidence_id = ?", (evidence_id,)).fetchone()
    return dict(row) if row else None


def record_extraction_outcome(conn, evidence_id, status, call_id, *, decided_by):
    if status not in ("candidate", "skipped"):
        raise ValueError(f"outcome status must be 'candidate' or 'skipped', got {status!r}")
    conn.execute(
        "INSERT INTO evidence_extraction (evidence_id, status, attempts, decided_by, last_call_id, updated_at) "
        "VALUES (?, ?, 0, ?, ?, ?) ON CONFLICT(evidence_id) DO UPDATE SET status = excluded.status, "
        "decided_by = excluded.decided_by, last_call_id = excluded.last_call_id, updated_at = excluded.updated_at",
        (evidence_id, status, decided_by, call_id, utc_now()),
    )


def record_extraction_failure(conn, evidence_id, call_id, *, count_attempt, max_attempts):
    """Record a failed attempt. Returns the new status: 'retryable', or 'dead' once max_attempts counted failures are reached."""
    row = conn.execute("SELECT attempts FROM evidence_extraction WHERE evidence_id = ?", (evidence_id,)).fetchone()
    attempts = (row["attempts"] if row else 0) + (1 if count_attempt else 0)
    status = "dead" if attempts >= max_attempts else "retryable"
    conn.execute(
        "INSERT INTO evidence_extraction (evidence_id, status, attempts, decided_by, last_call_id, updated_at) "
        "VALUES (?, ?, ?, NULL, ?, ?) ON CONFLICT(evidence_id) DO UPDATE SET status = excluded.status, "
        "attempts = excluded.attempts, last_call_id = excluded.last_call_id, updated_at = excluded.updated_at",
        (evidence_id, status, attempts, call_id, utc_now()),
    )
    return status
```

- [ ] **Step 4: Replace `extract.py`**

Keep `SIGNAL_TYPES` and `EXTRACTION_PROMPT_TEMPLATE` exactly as they are. The full new module:

```python
import json
from collections import Counter

import anthropic

import db
import ledger
from credentials import get_secret
from llm import reply_from_response

SIGNAL_TYPES = {
    # ... unchanged: the 12 existing signal types ...
}

EXTRACTION_MODEL = "claude-sonnet-5"
MAX_EXTRACTION_ATTEMPTS = 3
MAX_CONSECUTIVE_API_ERRORS = 5

EXTRACTION_PROMPT_TEMPLATE = """..."""  # unchanged


class ExtractionError(Exception):
    pass


class ExtractionAPIError(ExtractionError):
    """The API call itself failed (rate limit, network, etc.) -- not a model mistake."""


class ExtractionResponseError(ExtractionError):
    """The model responded, but the response was unusable (non-JSON, missing keys, bad signal_type)."""


def build_extraction_prompt(evidence: dict) -> str:
    return EXTRACTION_PROMPT_TEMPLATE.format(
        title=evidence["title"], content=evidence["content"],
        signal_types=", ".join(f'"{t}"' for t in sorted(SIGNAL_TYPES)),
    )


def _label(evidence):
    return evidence.get("evidence_id", "<no evidence_id>")


def request_extraction(client, evidence):
    """Make the extraction API call. Returns an LLMReply; raises ExtractionAPIError if the call itself fails."""
    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": build_extraction_prompt(evidence)}],
        )
    except Exception as e:
        raise ExtractionAPIError(f"evidence {_label(evidence)}: API call failed: {e}") from e
    return reply_from_response(response)


def parse_extraction_reply(reply, evidence_label):
    if reply.text is None:
        raise ExtractionResponseError(
            f"evidence {evidence_label}: response has no text block (stop_reason={reply.stop_reason!r})"
        )
    return parse_extraction_text(reply.text, evidence_label)


def parse_extraction_text(raw_text, evidence_label):
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ExtractionResponseError(f"evidence {evidence_label}: non-JSON response: {raw_text!r}") from e

    if not isinstance(parsed, dict):
        raise ExtractionResponseError(f"evidence {evidence_label}: expected a JSON object, got {raw_text!r}")

    if parsed.get("skip"):
        return None

    missing = {"signal_type", "summary", "confidence"} - parsed.keys()
    if missing:
        raise ExtractionResponseError(f"evidence {evidence_label}: response missing keys {missing}")

    if not isinstance(parsed["signal_type"], str) or parsed["signal_type"] not in SIGNAL_TYPES:
        raise ExtractionResponseError(f"evidence {evidence_label}: invalid signal_type {parsed['signal_type']!r}")

    entity = parsed.get("entity")
    return {
        "signal_type": parsed["signal_type"],
        "summary": parsed["summary"],
        "confidence": parsed["confidence"],
        "entity": entity if isinstance(entity, dict) else None,
        "effective_date": parsed.get("effective_date"),
    }


def extract_topic(client, evidence: dict):
    return parse_extraction_reply(request_extraction(client, evidence), _label(evidence))


def extract_one(conn, client, evidence):
    """One evidence row through the effect sandwich: record intent (committed), call the model,
    then commit the outcome together with the ledger result. Returns the outcome name."""
    evidence_id = evidence["evidence_id"]
    call_id = ledger.start_call(
        conn, stage="extract", subject_key=evidence_id, model=EXTRACTION_MODEL,
        prompt_template=EXTRACTION_PROMPT_TEMPLATE,
    )

    try:
        reply = request_extraction(client, evidence)
    except ExtractionAPIError as e:
        ledger.finish_call(conn, call_id, status="api_error", error=str(e))
        db.record_extraction_failure(conn, evidence_id, call_id, count_attempt=False,
                                     max_attempts=MAX_EXTRACTION_ATTEMPTS)
        conn.commit()
        print(f"extraction API error, will retry next run: {e}")
        return "api_error"

    try:
        result = parse_extraction_reply(reply, evidence_id)
    except ExtractionResponseError as e:
        ledger.finish_call(conn, call_id, status="response_error", reply=reply, error=str(e))
        status = db.record_extraction_failure(conn, evidence_id, call_id, count_attempt=True,
                                              max_attempts=MAX_EXTRACTION_ATTEMPTS)
        conn.commit()
        print(f"unusable extraction response ({status}): {e}")
        return "response_error"

    ledger.finish_call(conn, call_id, status="succeeded", reply=reply,
                       parsed=result if result is not None else {"skip": True})
    if result is None:
        db.record_extraction_outcome(conn, evidence_id, "skipped", call_id, decided_by="extractor")
        outcome = "skipped"
    else:
        db.insert_signal_candidate(
            conn,
            evidence_id=evidence_id,
            signal_type=result["signal_type"],
            summary=result["summary"],
            confidence=result["confidence"],
            entity=result["entity"],
            effective_date=result["effective_date"],
            llm_call_id=call_id,
        )
        db.record_extraction_outcome(conn, evidence_id, "candidate", call_id, decided_by="extractor")
        outcome = "candidate"
    conn.commit()
    return outcome


def run(db_path="data/pi_agent.db", today=None, client=None):
    client = client or anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    conn = db.connect(db_path)
    db.init_db(conn)
    counts = Counter()
    try:
        abandoned = ledger.abandon_stale_calls(conn, "extract")
        if abandoned:
            print(f"marked {abandoned} interrupted extraction call(s) abandoned; their evidence will be retried")
        consecutive_api_errors = 0
        for evidence in db.get_evidence_pending_extraction(conn):
            outcome = extract_one(conn, client, evidence)
            counts[outcome] += 1
            consecutive_api_errors = consecutive_api_errors + 1 if outcome == "api_error" else 0
            if consecutive_api_errors >= MAX_CONSECUTIVE_API_ERRORS:
                raise ExtractionAPIError(
                    f"stopping after {consecutive_api_errors} consecutive API errors; rerun when the API is healthy"
                )
    finally:
        conn.close()
    print(("extraction: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))) if counts
          else "extraction: nothing pending")


if __name__ == "__main__":
    run()
```

`parse_extraction_text` is the old parsing body, moved without behavior changes. P3 Task 2 tightens it.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_extract.py tests/test_end_to_end.py tests/test_evals_harness.py tests/test_evals_runners.py -v`
Expected: PASS. The existing extraction, harness and runner tests pass unchanged: `extract_topic`'s behavior and error messages are the same.

- [ ] **Step 6: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add db.py extract.py tests/test_extract.py
git commit -m "feat(extract): commit each extraction with its ledger entry; record skips and failures"
```

---

### Task 6: Safe application of matching decisions

**Files:**
- Modify: `db.py` (migration 4; `set_candidate_topic`, `get_candidates_without_topic`, `record_match_failure`, `get_candidates_by_ids`)
- Modify: `match.py` (add `normalize_slug`, `Resolution`, `resolve_decisions`, `ApplyOutcome`, `apply_resolutions`; rewrite `apply_matches`)
- Test: `tests/test_match_resolution.py` (create), `tests/test_match.py` (append)

**Interfaces:**
- Consumes: nothing new from earlier tasks.
- Produces:
  - Columns `signal_candidate.match_call_id INTEGER`, `match_attempts INTEGER NOT NULL DEFAULT 0`, `match_error TEXT`
  - `db.set_candidate_topic(conn, candidate_id, topic_id, match_call_id=None)`
  - `db.record_match_failure(conn, candidate_id, reason)`
  - `db.get_candidates_without_topic(conn, max_attempts=None)`
  - `db.get_candidates_by_ids(conn, candidate_ids) -> list[dict]` (in the given order)
  - `match.normalize_slug(text) -> str`
  - `match.Resolution` and `match.resolve_decisions(candidates, decisions, existing_topics) -> list[Resolution]` (spec §4.6; P2 adds `redirects`)
  - `match.ApplyOutcome(matched: int, created: list[str], invalid: list[tuple[str, str]])`
  - `match.apply_resolutions(conn, candidates, resolutions, week, match_call_id=None) -> ApplyOutcome`
  - `match.apply_matches(conn, candidates, decisions, week) -> ApplyOutcome`
  - `match.MAX_MATCH_ATTEMPTS = 3`

- [ ] **Step 1: Write the failing pure-function tests**

Create `tests/test_match_resolution.py`:

```python
import pytest

import match

TOPICS = [{"topic_id": "TOPIC-0001", "slug": "dark-mode-support", "name": "Dark mode", "description": "d"}]
CANDS = [{"candidate_id": f"SC-2026-0000{i}"} for i in range(1, 4)]


def actions(resolutions):
    return [(r.action, r.topic_id, r.new_topic["slug"] if r.new_topic else None) for r in resolutions]


@pytest.mark.parametrize("text,slug", [
    ("Export to CSV", "export-to-csv"),
    ("  --Weird__Slug!! ", "weird-slug"),
    ("export-to-csv", "export-to-csv"),
    (None, ""),
    ("!!!", ""),
])
def test_normalize_slug(text, slug):
    assert match.normalize_slug(text) == slug


def test_valid_match_and_create():
    decisions = [
        {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None},
        {"index": 1, "matched_topic_id": None,
         "new_topic": {"name": "Export to CSV", "slug": "export-to-csv", "description": "wants CSV"}},
    ]
    assert actions(match.resolve_decisions(CANDS[:2], decisions, TOPICS)) == [
        ("match", "TOPIC-0001", None), ("create", None, "export-to-csv"),
    ]


def test_missing_and_duplicate_decisions_are_invalid():
    decisions = [{"index": 1, "matched_topic_id": "TOPIC-0001"}, {"index": 1, "matched_topic_id": "TOPIC-0001"}]
    resolutions = match.resolve_decisions(CANDS[:2], decisions, TOPICS)
    assert [(r.action, r.reason) for r in resolutions] == [("invalid", "no decision"), ("invalid", "2 decisions")]


def test_unknown_topic_id_is_invalid():
    [r] = match.resolve_decisions(CANDS[:1], [{"index": 0, "matched_topic_id": "TOPIC-9999"}], TOPICS)
    assert (r.action, r.reason) == ("invalid", "unknown topic id 'TOPIC-9999'")


def test_both_fields_set_is_invalid():
    decision = {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": {"name": "x", "slug": "x", "description": "y"}}
    [r] = match.resolve_decisions(CANDS[:1], [decision], TOPICS)
    assert r.action == "invalid"


def test_new_topic_with_existing_slug_becomes_a_match():
    decision = {"index": 0, "matched_topic_id": None,
                "new_topic": {"name": "Dark Mode Support", "slug": "Dark-Mode-Support", "description": "d"}}
    [r] = match.resolve_decisions(CANDS[:1], [decision], TOPICS)
    assert (r.action, r.topic_id) == ("match", "TOPIC-0001")


def test_new_topic_slug_falls_back_to_name():
    decision = {"index": 0, "matched_topic_id": None, "new_topic": {"name": "Export to CSV", "slug": "", "description": "d"}}
    [r] = match.resolve_decisions(CANDS[:1], [decision], TOPICS)
    assert r.new_topic == {"name": "Export to CSV", "slug": "export-to-csv", "description": "d"}


@pytest.mark.parametrize("decisions", [None, "nope", {"index": 0}, [1, 2, 3], [{"index": "0"}], [{"index": True}]])
def test_non_list_or_garbage_output_never_raises(decisions):
    resolutions = match.resolve_decisions(CANDS, decisions, TOPICS)
    assert [r.action for r in resolutions] == ["invalid"] * 3
```

- [ ] **Step 2: Write the failing database-side tests**

Append to `tests/test_match.py`:

```python
def test_apply_matches_groups_candidates_proposing_the_same_new_slug(tmp_path):
    conn = seed_db(tmp_path)
    first = seed_candidate(conn, "wants CSV export")
    second = seed_candidate(conn, "CSV export please")
    candidates = db.get_candidates_without_topic(conn)
    new_topic = {"name": "Export to CSV", "slug": "export-to-csv", "description": "CSV export"}
    decisions = [{"index": 0, "matched_topic_id": None, "new_topic": new_topic},
                 {"index": 1, "matched_topic_id": None, "new_topic": dict(new_topic)}]

    outcome = match.apply_matches(conn, candidates, decisions, week="2026-W33")
    conn.commit()

    topic = db.get_canonical_topic_by_slug(conn, "export-to-csv")
    assert len(db.get_canonical_topics(conn)) == 1
    assert len(outcome.created) == 1
    rows = conn.execute("SELECT candidate_id, topic_id FROM signal_candidate ORDER BY candidate_id").fetchall()
    assert [(r["candidate_id"], r["topic_id"]) for r in rows] == [(first, topic["topic_id"]), (second, topic["topic_id"])]
    assert db.get_topic_weekly_mentions(conn, topic["topic_id"]) == {"2026-W33": 2}


def test_apply_matches_applies_valid_decisions_and_records_invalid_ones(tmp_path):
    conn = seed_db(tmp_path)
    topic_id = seed_existing_topic(conn)
    good = seed_candidate(conn, "dark mode again")
    bad = seed_candidate(conn, "something")
    candidates = db.get_candidates_without_topic(conn)
    decisions = [{"index": 0, "matched_topic_id": topic_id}, {"index": 1, "matched_topic_id": "TOPIC-9999"}]

    outcome = match.apply_matches(conn, candidates, decisions, week="2026-W33")
    conn.commit()

    assert outcome.matched == 1
    assert outcome.invalid == [(bad, "unknown topic id 'TOPIC-9999'")]
    rows = {r["candidate_id"]: dict(r) for r in conn.execute(
        "SELECT candidate_id, topic_id, match_attempts, match_error FROM signal_candidate")}
    assert rows[good]["topic_id"] == topic_id
    assert (rows[bad]["topic_id"], rows[bad]["match_attempts"], rows[bad]["match_error"]) == (
        None, 1, "unknown topic id 'TOPIC-9999'")


def test_apply_matches_survives_a_missing_index_without_key_error(tmp_path):
    conn = seed_db(tmp_path)
    seed_candidate(conn, "a")
    candidates = db.get_candidates_without_topic(conn)

    outcome = match.apply_matches(conn, candidates, [], week="2026-W33")

    assert outcome.invalid[0][1] == "no decision"


def test_candidates_at_max_attempts_are_excluded(tmp_path):
    conn = seed_db(tmp_path)
    stuck = seed_candidate(conn, "a")
    fresh = seed_candidate(conn, "b")
    for _ in range(match.MAX_MATCH_ATTEMPTS):
        db.record_match_failure(conn, stuck, "bad")
    conn.commit()

    remaining = db.get_candidates_without_topic(conn, max_attempts=match.MAX_MATCH_ATTEMPTS)

    assert [c["candidate_id"] for c in remaining] == [fresh]
    assert len(db.get_candidates_without_topic(conn)) == 2


def test_get_candidates_by_ids_keeps_the_given_order(tmp_path):
    conn = seed_db(tmp_path)
    a = seed_candidate(conn, "a")
    b = seed_candidate(conn, "b")
    assert [c["candidate_id"] for c in db.get_candidates_by_ids(conn, [b, a])] == [b, a]
```

`seed_candidate` in `tests/test_match.py` inserts evidence with the fixed `source_url="/x"`. That's fine: `source_key` stays `NULL` for direct `insert_evidence` calls.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_match_resolution.py tests/test_match.py -v`
Expected: FAIL (`AttributeError: module 'match' has no attribute 'normalize_slug'`, `no such column: match_attempts`).

- [ ] **Step 4: Add migration 4 and the helpers to `db.py`**

```python
def _migration_4_candidate_matching(conn):
    _add_column(conn, "signal_candidate", "match_call_id INTEGER REFERENCES llm_call(call_id)")
    _add_column(conn, "signal_candidate", "match_attempts INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "signal_candidate", "match_error TEXT")
```

Register it as `(4, _migration_4_candidate_matching)`. Replace `set_candidate_topic` and `get_candidates_without_topic`, and add the two new functions:

```python
def set_candidate_topic(conn, candidate_id, topic_id, match_call_id=None):
    conn.execute(
        "UPDATE signal_candidate SET topic_id = ?, match_call_id = COALESCE(?, match_call_id), match_error = NULL "
        "WHERE candidate_id = ?",
        (topic_id, match_call_id, candidate_id),
    )


def record_match_failure(conn, candidate_id, reason):
    conn.execute(
        "UPDATE signal_candidate SET match_attempts = match_attempts + 1, match_error = ? WHERE candidate_id = ?",
        (reason, candidate_id),
    )


def get_candidates_without_topic(conn, max_attempts=None):
    sql, params = "SELECT * FROM signal_candidate WHERE topic_id IS NULL", ()
    if max_attempts is not None:
        sql, params = sql + " AND match_attempts < ?", (max_attempts,)
    rows = conn.execute(sql + " ORDER BY candidate_id", params).fetchall()
    return [_signal_candidate_row_to_dict(r) for r in rows]


def get_candidates_by_ids(conn, candidate_ids):
    rows = {r["candidate_id"]: r for r in conn.execute(
        f"SELECT * FROM signal_candidate WHERE candidate_id IN ({','.join('?' * len(candidate_ids))})",
        tuple(candidate_ids),
    )} if candidate_ids else {}
    return [_signal_candidate_row_to_dict(rows[i]) for i in candidate_ids if i in rows]
```

- [ ] **Step 5: Add resolution and safe apply to `match.py`**

Add `import re` and `from dataclasses import dataclass, field` to the imports. Add `MAX_MATCH_ATTEMPTS = 3` after `MATCH_MODEL`. Replace `apply_matches` with:

```python
_SLUG_JUNK_RE = re.compile(r"[^a-z0-9]+")


def normalize_slug(text):
    """Lowercase, hyphen-separated, [a-z0-9] only. Returns "" when nothing usable is left."""
    return _SLUG_JUNK_RE.sub("-", text.lower()).strip("-") if isinstance(text, str) else ""


@dataclass
class Resolution:
    """What to do with one candidate, decided from the matcher's output before touching the database."""
    index: int
    action: str  # "match" | "create" | "invalid"
    topic_id: str | None = None
    new_topic: dict | None = None
    reason: str | None = None


def resolve_decisions(candidates, decisions, existing_topics):
    """Turn raw matcher output into one Resolution per candidate. Pure and total: it never raises,
    whatever `decisions` contains, so no model output can crash the stage."""
    known_ids = {t["topic_id"] for t in existing_topics}
    id_by_slug = {t["slug"]: t["topic_id"] for t in existing_topics if t.get("slug")}

    if not isinstance(decisions, list):
        reason = f"matcher returned {type(decisions).__name__}, expected a list"
        return [Resolution(i, "invalid", reason=reason) for i in range(len(candidates))]

    by_index = {}
    for decision in decisions:
        if isinstance(decision, dict):
            index = decision.get("index")
            if isinstance(index, int) and not isinstance(index, bool):
                by_index.setdefault(index, []).append(decision)

    resolutions = []
    for i in range(len(candidates)):
        got = by_index.get(i, [])
        if len(got) != 1:
            resolutions.append(Resolution(i, "invalid", reason="no decision" if not got else f"{len(got)} decisions"))
        else:
            resolutions.append(_resolve_one(i, got[0], known_ids, id_by_slug))
    return resolutions


def _resolve_one(index, decision, known_ids, id_by_slug):
    topic_id = decision.get("matched_topic_id")
    new_topic = decision.get("new_topic")
    if topic_id:
        if new_topic:
            return Resolution(index, "invalid", reason="both matched_topic_id and new_topic are set")
        if not isinstance(topic_id, str) or topic_id not in known_ids:
            return Resolution(index, "invalid", reason=f"unknown topic id {topic_id!r}")
        return Resolution(index, "match", topic_id=topic_id)

    if not isinstance(new_topic, dict):
        return Resolution(index, "invalid", reason="neither matched_topic_id nor new_topic is set")
    name, description = new_topic.get("name"), new_topic.get("description")
    if not (isinstance(name, str) and name.strip() and isinstance(description, str) and description.strip()):
        return Resolution(index, "invalid", reason="new_topic needs a non-empty name and description")
    slug = normalize_slug(new_topic.get("slug")) or normalize_slug(name)
    if not slug:
        return Resolution(index, "invalid", reason="new_topic has no usable slug")
    if slug in id_by_slug:
        return Resolution(index, "match", topic_id=id_by_slug[slug], reason=f"new topic slug {slug!r} already exists")
    return Resolution(index, "create", new_topic={"name": name.strip(), "slug": slug, "description": description.strip()})


@dataclass
class ApplyOutcome:
    matched: int = 0
    created: list = field(default_factory=list)
    invalid: list = field(default_factory=list)


def apply_resolutions(conn, candidates, resolutions, week, match_call_id=None):
    """Write resolutions to the database. Does not commit. Invalid ones only count an attempt."""
    outcome = ApplyOutcome()
    created_by_slug = {}
    for candidate, resolution in zip(candidates, resolutions):
        candidate_id = candidate["candidate_id"]
        if resolution.action == "invalid":
            db.record_match_failure(conn, candidate_id, resolution.reason)
            outcome.invalid.append((candidate_id, resolution.reason))
            continue

        if resolution.action == "match":
            topic_id = resolution.topic_id
            db.update_topic_last_seen(conn, topic_id, week)
            outcome.matched += 1
        else:
            slug = resolution.new_topic["slug"]
            topic_id = created_by_slug.get(slug)
            if topic_id is not None:
                outcome.matched += 1
            else:
                existing = db.get_canonical_topic_by_slug(conn, slug)
                if existing is not None:
                    topic_id = existing["topic_id"]
                    db.update_topic_last_seen(conn, topic_id, week)
                    outcome.matched += 1
                else:
                    topic_id = db.insert_canonical_topic(
                        conn, slug=slug, name=resolution.new_topic["name"],
                        description=resolution.new_topic["description"], aliases=[],
                        first_seen=week, last_seen=week,
                    )
                    outcome.created.append(topic_id)
                created_by_slug[slug] = topic_id

        db.set_candidate_topic(conn, candidate_id, topic_id, match_call_id=match_call_id)
        db.increment_topic_weekly_mentions(conn, topic_id, week, amount=1)
    return outcome


def apply_matches(conn, candidates, decisions, week):
    existing = db.get_canonical_topics(conn)
    return apply_resolutions(conn, candidates, resolve_decisions(candidates, decisions, existing), week)
```

Leave `match.run` as it is in this task; Task 7 rewrites it. It still calls `apply_matches`, which now works through resolutions.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_match_resolution.py tests/test_match.py tests/test_end_to_end.py -v`
Expected: PASS, including the three existing `apply_matches` tests.

- [ ] **Step 7: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add db.py match.py tests/test_match_resolution.py tests/test_match.py
git commit -m "feat(match): resolve matcher output per candidate so no response can crash the stage"
```

---

### Task 7: Durable matching: stored responses, replay, narrow retry

**Files:**
- Modify: `match.py` (call path and `run`; full final module below)
- Test: `tests/test_match.py` (append)

**Interfaces:**
- Consumes: `ledger.*`, `llm.reply_from_response` (Task 3); `resolve_decisions`, `apply_resolutions`, `ApplyOutcome`, `MAX_MATCH_ATTEMPTS`, `db.get_candidates_by_ids`, `db.record_match_failure` (Task 6).
- Produces:
  - `match.request_matching(client, candidates, existing_topics) -> LLMReply` (raises `MatchAPIError`)
  - `match.parse_matching_reply(reply)` (raises `MatchResponseError`)
  - `match.call_matcher(client, candidates, existing_topics)` (unchanged contract)
  - `match.match_batch(conn, client, candidates, week) -> ApplyOutcome`
  - `match.replay_unapplied(conn) -> int`
  - `match.MATCH_RETRY_ROUNDS = 1`
  - Match ledger rows: `stage="match"`, `request_meta={"week", "candidate_ids", "topic_ids"}`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_match.py`:

```python
from datetime import date

import ledger


class SimulatedCrash(BaseException):
    pass


class RecordingMessages(FakeMessages):
    def __init__(self, responses):
        super().__init__(responses)
        self.prompts = []

    def create(self, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        return super().create(**kwargs)


def recording_client(responses):
    client = FakeClient([])
    client.messages = RecordingMessages(responses)
    return client


def test_run_stores_the_response_before_applying_and_marks_it_applied(tmp_path):
    conn = seed_db(tmp_path)
    seed_candidate(conn, "User wants CSV export")
    conn.close()
    response = json.dumps([{"index": 0, "matched_topic_id": None,
                            "new_topic": {"name": "Export to CSV", "slug": "export-to-csv", "description": "d"}}])

    match.run(db_path=str(tmp_path / "pi_agent.db"), today=date(2026, 8, 15), client=FakeClient([response]))

    conn = db.connect(str(tmp_path / "pi_agent.db"))
    [call] = [dict(r) for r in conn.execute("SELECT * FROM llm_call WHERE stage = 'match'")]
    assert call["status"] == "succeeded" and call["applied_at"] is not None
    assert json.loads(call["request_meta"])["week"] == "2026-W33"
    row = conn.execute("SELECT match_call_id FROM signal_candidate").fetchone()
    assert row["match_call_id"] == call["call_id"]


def test_run_replays_stored_response_after_crash_without_calling_api(tmp_path, monkeypatch):
    conn = seed_db(tmp_path)
    seed_candidate(conn, "User wants CSV export")
    conn.close()
    db_path = str(tmp_path / "pi_agent.db")
    response = json.dumps([{"index": 0, "matched_topic_id": None,
                            "new_topic": {"name": "Export to CSV", "slug": "export-to-csv", "description": "d"}}])

    real_apply = match.apply_resolutions

    def crash(*args, **kwargs):
        raise SimulatedCrash()

    monkeypatch.setattr(match, "apply_resolutions", crash)
    with pytest.raises(SimulatedCrash):
        match.run(db_path=db_path, today=date(2026, 8, 15), client=FakeClient([response]))

    monkeypatch.setattr(match, "apply_resolutions", real_apply)
    match.run(db_path=db_path, today=date(2026, 8, 22), client=FakeClient([]))  # IndexError if it calls the API

    conn = db.connect(db_path)
    topic = db.get_canonical_topic_by_slug(conn, "export-to-csv")
    assert topic["first_seen"] == "2026-W33"  # applied with the stored call's week, not the replay's
    assert ledger.get_unapplied_calls(conn, "match") == []


def test_retry_round_sends_only_the_candidates_that_failed(tmp_path):
    conn = seed_db(tmp_path)
    topic_id = seed_existing_topic(conn)
    seed_candidate(conn, "dark mode again")
    seed_candidate(conn, "unclear one")
    conn.close()
    first = json.dumps([{"index": 0, "matched_topic_id": topic_id}, {"index": 1, "matched_topic_id": "TOPIC-9999"}])
    retry = json.dumps([{"index": 0, "matched_topic_id": topic_id}])
    client = recording_client([first, retry])

    match.run(db_path=str(tmp_path / "pi_agent.db"), today=date(2026, 8, 15), client=client)

    assert len(client.messages.prompts) == 2
    assert "dark mode again" not in client.messages.prompts[1]
    assert "unclear one" in client.messages.prompts[1]
    conn = db.connect(str(tmp_path / "pi_agent.db"))
    assert db.get_candidates_without_topic(conn) == []


def test_api_error_is_recorded_and_raised(tmp_path):
    conn = seed_db(tmp_path)
    seed_candidate(conn, "a")
    conn.close()

    with pytest.raises(match.MatchAPIError):
        match.run(db_path=str(tmp_path / "pi_agent.db"), today=date(2026, 8, 15),
                  client=FakeClient([RuntimeError("down")]))

    conn = db.connect(str(tmp_path / "pi_agent.db"))
    assert conn.execute("SELECT status FROM llm_call").fetchone()[0] == "api_error"
    assert conn.execute("SELECT match_attempts FROM signal_candidate").fetchone()[0] == 0


def test_response_error_counts_an_attempt_for_every_candidate_and_raises(tmp_path):
    conn = seed_db(tmp_path)
    seed_candidate(conn, "a")
    seed_candidate(conn, "b")
    conn.close()

    with pytest.raises(match.MatchResponseError):
        match.run(db_path=str(tmp_path / "pi_agent.db"), today=date(2026, 8, 15), client=FakeClient(["not json"]))

    conn = db.connect(str(tmp_path / "pi_agent.db"))
    assert [r[0] for r in conn.execute("SELECT match_attempts FROM signal_candidate")] == [1, 1]
    assert conn.execute("SELECT raw_response FROM llm_call").fetchone()[0] == "not json"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_match.py -v`
Expected: the new tests FAIL (no `llm_call` rows for `match`; `apply_resolutions` isn't reached through `run`).

- [ ] **Step 3: Replace `match.py` with the final module**

Keep `MATCHING_PROMPT_TEMPLATE` and `build_matching_prompt` exactly as they are, and keep the Task 6 code (`normalize_slug` through `apply_matches`) as written. The full module:

```python
import json
import re
from dataclasses import dataclass, field
from datetime import date

import anthropic

import db
import ledger
from credentials import get_secret
from llm import reply_from_response
from weekutil import iso_week_string

MATCH_MODEL = "claude-sonnet-5"
MAX_MATCH_ATTEMPTS = 3
MATCH_RETRY_ROUNDS = 1

MATCHING_PROMPT_TEMPLATE = """..."""  # unchanged


class MatchError(Exception):
    pass


class MatchAPIError(MatchError):
    """The API call itself failed (rate limit, network, etc.) -- not a model mistake."""


class MatchResponseError(MatchError):
    """The model responded, but the response was unusable (no text block, or not valid JSON)."""


def build_matching_prompt(candidates, existing_topics):
    ...  # unchanged


def request_matching(client, candidates, existing_topics):
    try:
        response = client.messages.create(
            model=MATCH_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": build_matching_prompt(candidates, existing_topics)}],
        )
    except Exception as e:
        raise MatchAPIError(f"matching call failed: {e}") from e
    return reply_from_response(response)


def parse_matching_reply(reply):
    if reply.text is None:
        raise MatchResponseError(f"matching response has no text block (stop_reason={reply.stop_reason!r})")
    try:
        return json.loads(reply.text)
    except json.JSONDecodeError as e:
        raise MatchResponseError(f"matching call returned non-JSON response: {reply.text!r}") from e


def call_matcher(client, candidates, existing_topics):
    if not candidates:
        return []
    return parse_matching_reply(request_matching(client, candidates, existing_topics))


# --- Task 6 code: normalize_slug, Resolution, resolve_decisions, _resolve_one,
# --- ApplyOutcome, apply_resolutions, apply_matches (unchanged) ---


def match_batch(conn, client, candidates, week):
    """One matching call through the effect sandwich. The response is committed before it is
    applied, so a crash while applying loses no paid work: replay_unapplied() finishes the job."""
    existing = db.get_canonical_topics(conn)
    call_id = ledger.start_call(
        conn, stage="match", subject_key=f"{week}:{candidates[0]['candidate_id']}", model=MATCH_MODEL,
        prompt_template=MATCHING_PROMPT_TEMPLATE,
        request_meta={
            "week": week,
            "candidate_ids": [c["candidate_id"] for c in candidates],
            "topic_ids": [t["topic_id"] for t in existing],
        },
    )
    try:
        reply = request_matching(client, candidates, existing)
    except MatchAPIError as e:
        ledger.finish_call(conn, call_id, status="api_error", error=str(e))
        conn.commit()
        raise
    try:
        decisions = parse_matching_reply(reply)
    except MatchResponseError as e:
        ledger.finish_call(conn, call_id, status="response_error", reply=reply, error=str(e))
        for candidate in candidates:
            db.record_match_failure(conn, candidate["candidate_id"], str(e))
        conn.commit()
        raise

    ledger.finish_call(conn, call_id, status="succeeded", reply=reply, parsed=decisions)
    conn.commit()
    return _apply_call(conn, call_id, candidates, decisions, week, existing)


def _apply_call(conn, call_id, candidates, decisions, week, existing):
    resolutions = resolve_decisions(candidates, decisions, existing)
    outcome = apply_resolutions(conn, candidates, resolutions, week, match_call_id=call_id)
    ledger.mark_applied(conn, call_id)
    conn.commit()
    return outcome


def replay_unapplied(conn):
    """Apply matching responses that were paid for and stored but never applied (a crash between
    the two commits). Uses the week recorded with the call. Returns how many were replayed."""
    replayed = 0
    for call in ledger.get_unapplied_calls(conn, "match"):
        meta = call["request_meta"] or {}
        candidates = db.get_candidates_by_ids(conn, meta.get("candidate_ids", []))
        if len(candidates) != len(meta.get("candidate_ids", [])) or any(c["topic_id"] for c in candidates):
            print(f"match call {call['call_id']}: candidates changed since the call; not replaying it")
            ledger.mark_applied(conn, call["call_id"])
            conn.commit()
            continue
        _apply_call(conn, call["call_id"], candidates, call["parsed"], meta["week"], db.get_canonical_topics(conn))
        replayed += 1
    return replayed


def run(db_path="data/pi_agent.db", today=None, client=None):
    week = iso_week_string(today or date.today())
    conn = db.connect(db_path)
    db.init_db(conn)

    client = client or anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    try:
        ledger.abandon_stale_calls(conn, "match")
        replayed = replay_unapplied(conn)
        if replayed:
            print(f"matching: applied {replayed} stored response(s) from an interrupted run")
        for _ in range(1 + MATCH_RETRY_ROUNDS):
            candidates = db.get_candidates_without_topic(conn, max_attempts=MAX_MATCH_ATTEMPTS)
            if not candidates:
                break
            outcome = match_batch(conn, client, candidates, week)
            print(f"matching: {outcome.matched} matched, {len(outcome.created)} new topics, "
                  f"{len(outcome.invalid)} invalid")
            if not outcome.invalid:
                break
    finally:
        conn.close()


if __name__ == "__main__":
    run()
```

The narrow retry needs no special code. After the first round, the only unmatched candidates under the attempt limit are the ones whose decisions were invalid, so the second round sends only those.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_match.py tests/test_match_resolution.py tests/test_end_to_end.py tests/test_evals_runners.py tests/test_evals_harness.py -v`
Expected: PASS. The eval tests still pass because `call_matcher` keeps its contract and error messages.

- [ ] **Step 5: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add match.py tests/test_match.py
git commit -m "feat(match): store matcher responses before applying, replay after crashes, retry invalid candidates once"
```

---

### Task 8: Materiality: one signal per topic and week, events through an outbox

**Files:**
- Modify: `db.py` (migration 5; `insert_material_signal` gains `week`; outbox helpers)
- Modify: `materiality.py` (`build_envelope`, `emit_event`, `flush_outbox`, `run`)
- Test: `tests/test_materiality.py` (append)

**Interfaces:**
- Consumes: `db.MIGRATIONS`, `db.utc_now`.
- Produces:
  - Column `material_signal.week TEXT`; index `idx_material_signal_topic_week`
  - Table `event_outbox(outbox_id INTEGER PK AUTOINCREMENT, signal_id TEXT UNIQUE, envelope TEXT, created_at, emitted_at)`
  - `db.insert_material_signal(conn, *, ..., week=None) -> str`
  - `db.get_latest_material_signal_for_topic_week(conn, topic_id, week) -> dict | None`
  - `db.enqueue_event(conn, signal_id, envelope)`, `db.get_pending_outbox(conn) -> list[dict]`, `db.mark_outbox_emitted(conn, outbox_id)`
  - `materiality.build_envelope(material_signal) -> dict`
  - `materiality.flush_outbox(conn, events_path=DEFAULT_EVENTS_PATH) -> int`
  - `materiality.LABEL_RANK`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_materiality.py`:

```python
import sqlite3


def _seed_high_topic(db_path):
    """A topic that crosses the 'high' threshold in 2026-W33 (same shape as the existing high test)."""
    conn = db.connect(db_path)
    db.init_db(conn)
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )
    for week in ("2026-W30", "2026-W31", "2026-W32"):
        db.increment_topic_weekly_mentions(conn, topic_id, week, amount=1)
    for i in range(6):
        evidence_id = db.insert_evidence(
            conn, source_type="reddit_post", source_name="sub", source_url=f"/p{i}",
            captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
            title="t", content="c", metadata={},
        )
        db.insert_signal_candidate(conn, evidence_id=evidence_id, signal_type="new_feature_demand",
                                   summary="s", confidence=0.9, topic_id=topic_id)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W33", amount=6)
    conn.commit()
    conn.close()
    return topic_id


def _lines(path):
    with open(path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def test_rerunning_the_same_week_creates_no_duplicate_signal_or_event(tmp_path):
    db_path, events = str(tmp_path / "pi_agent.db"), tmp_path / "events.jsonl"
    _seed_high_topic(db_path)

    materiality.run(db_path=db_path, today=date(2026, 8, 15), events_path=str(events))
    materiality.run(db_path=db_path, today=date(2026, 8, 15), events_path=str(events))

    conn = db.connect(db_path)
    assert conn.execute("SELECT COUNT(*) FROM material_signal").fetchone()[0] == 1
    assert conn.execute("SELECT week FROM material_signal").fetchone()[0] == "2026-W33"
    assert len(_lines(events)) == 1


def test_escalation_within_a_week_emits_a_second_signal(tmp_path, monkeypatch):
    db_path, events = str(tmp_path / "pi_agent.db"), tmp_path / "events.jsonl"
    _seed_high_topic(db_path)
    materiality.run(db_path=db_path, today=date(2026, 8, 15), events_path=str(events))

    real = materiality.classify_materiality
    monkeypatch.setattr(materiality, "classify_materiality",
                        lambda **kw: {**real(**kw), "label": "critical", "score": 0.95})
    materiality.run(db_path=db_path, today=date(2026, 8, 15), events_path=str(events))

    conn = db.connect(db_path)
    labels = [r[0] for r in conn.execute("SELECT materiality_label FROM material_signal ORDER BY signal_id")]
    assert labels == ["high", "critical"]
    reasons = json.loads(conn.execute(
        "SELECT materiality_reasons FROM material_signal WHERE materiality_label = 'critical'").fetchone()[0])
    assert any(r.startswith("escalated from high") for r in reasons)
    assert len(_lines(events)) == 2


def test_event_committed_but_never_written_is_flushed_on_the_next_run(tmp_path, monkeypatch):
    db_path, events = str(tmp_path / "pi_agent.db"), tmp_path / "events.jsonl"
    _seed_high_topic(db_path)
    real_flush = materiality.flush_outbox
    calls = {"n": 0}

    def flush_then_crash_on_the_final_flush(conn, events_path):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("crash after commit, before writing events")
        return real_flush(conn, events_path)

    monkeypatch.setattr(materiality, "flush_outbox", flush_then_crash_on_the_final_flush)
    with pytest.raises(RuntimeError):
        materiality.run(db_path=db_path, today=date(2026, 8, 15), events_path=str(events))
    assert not events.exists()

    monkeypatch.setattr(materiality, "flush_outbox", real_flush)
    materiality.run(db_path=db_path, today=date(2026, 8, 15), events_path=str(events))

    assert len(_lines(events)) == 1


def test_flush_skips_signals_already_in_the_events_file(tmp_path):
    db_path, events = str(tmp_path / "pi_agent.db"), tmp_path / "events.jsonl"
    conn = db.connect(db_path)
    db.init_db(conn)
    envelope = {"event": materiality.EVENT_NAME, "signal": {"signal_id": "SIG-2026-0001"}}
    events.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
    db.insert_material_signal(
        conn, created_at="2026-08-15T00:00:00+00:00", signal_type="new_feature_demand", topic_id=None,
        entity={}, summary="s", confidence_label="high", materiality_label="high", materiality_score=0.8,
        materiality_reasons=[], evidence_ids=[], change_type="trend_change",
        recommended_next_step="strategic_assessment", week="2026-W33",
    )
    db.enqueue_event(conn, "SIG-2026-0001", envelope)
    conn.commit()

    assert materiality.flush_outbox(conn, str(events)) == 0
    assert db.get_pending_outbox(conn) == []
    assert len(_lines(events)) == 1


def test_migration_marks_existing_signals_as_already_emitted(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(db.SCHEMA_SQL)
    raw.execute(
        "INSERT INTO material_signal (signal_id, created_at, signal_type, topic_id, entity, summary, confidence_label, "
        "materiality_label, materiality_score, materiality_reasons, evidence_ids, change_type, recommended_next_step) "
        "VALUES ('SIG-2026-0001', '2026-08-15T00:00:00+00:00', 'x', NULL, '{}', 's', 'high', 'high', 0.8, '[]', "
        "'[]', 'trend_change', 'strategic_assessment')"
    )
    raw.commit()
    raw.close()

    conn = db.connect(str(path))
    db.init_db(conn)

    assert conn.execute("SELECT week FROM material_signal").fetchone()[0] == "2026-W33"
    assert db.get_pending_outbox(conn) == []
```

Add `import pytest` at the top of `tests/test_materiality.py` if it isn't there yet.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_materiality.py -v`
Expected: the new tests FAIL (`no such column: week`, `no attribute 'flush_outbox'`).

- [ ] **Step 3: Add migration 5 and the helpers to `db.py`**

Add `from weekutil import iso_week_string` to the imports of `db.py`. Then add:

```python
def _migration_5_signal_week_and_outbox(conn):
    _add_column(conn, "material_signal", "week TEXT")
    for row in conn.execute("SELECT signal_id, created_at FROM material_signal WHERE week IS NULL").fetchall():
        week = iso_week_string(datetime.fromisoformat(row["created_at"]).date())
        conn.execute("UPDATE material_signal SET week = ? WHERE signal_id = ?", (week, row["signal_id"]))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_material_signal_topic_week ON material_signal(topic_id, week)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS event_outbox (
          outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_id TEXT NOT NULL UNIQUE REFERENCES material_signal(signal_id),
          envelope TEXT NOT NULL,
          created_at TEXT NOT NULL,
          emitted_at TEXT
        )"""
    )
    # Signals created before the outbox existed were already appended to events.jsonl.
    conn.execute(
        "INSERT OR IGNORE INTO event_outbox (signal_id, envelope, created_at, emitted_at) "
        "SELECT signal_id, '{}', created_at, created_at FROM material_signal"
    )
```

Register it as `(5, _migration_5_signal_week_and_outbox)`. Replace `insert_material_signal`, and add the outbox helpers:

```python
def insert_material_signal(conn, *, created_at, signal_type, topic_id, entity, summary, confidence_label,
                            materiality_label, materiality_score, materiality_reasons, evidence_ids,
                            change_type, recommended_next_step, week=None):
    year = created_at[:4]
    signal_id = _next_sequence_id(conn, "material_signal", "signal_id", f"SIG-{year}-", 4)
    conn.execute(
        "INSERT INTO material_signal (signal_id, created_at, signal_type, topic_id, entity, summary, "
        "confidence_label, materiality_label, materiality_score, materiality_reasons, evidence_ids, "
        "change_type, recommended_next_step, week) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (signal_id, created_at, signal_type, topic_id, json.dumps(entity, sort_keys=True), summary,
         confidence_label, materiality_label, materiality_score, json.dumps(materiality_reasons),
         json.dumps(evidence_ids), change_type, recommended_next_step, week),
    )
    return signal_id


def get_latest_material_signal_for_topic_week(conn, topic_id, week):
    row = conn.execute(
        "SELECT * FROM material_signal WHERE topic_id = ? AND week = ? ORDER BY signal_id DESC LIMIT 1",
        (topic_id, week),
    ).fetchone()
    return _material_signal_row_to_dict(row) if row else None


def enqueue_event(conn, signal_id, envelope):
    conn.execute(
        "INSERT INTO event_outbox (signal_id, envelope, created_at) VALUES (?, ?, ?)",
        (signal_id, json.dumps(envelope), utc_now()),
    )


def get_pending_outbox(conn):
    rows = conn.execute("SELECT * FROM event_outbox WHERE emitted_at IS NULL ORDER BY outbox_id").fetchall()
    return [{**dict(r), "envelope": json.loads(r["envelope"])} for r in rows]


def mark_outbox_emitted(conn, outbox_id):
    conn.execute("UPDATE event_outbox SET emitted_at = ? WHERE outbox_id = ?", (utc_now(), outbox_id))
```

- [ ] **Step 4: Update `materiality.py`**

Add `LABEL_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}` after `MATERIALITY_LABEL_SCORE`. Replace `emit_event` and `run`, and add the new functions:

```python
def build_envelope(material_signal):
    return {
        "event": EVENT_NAME,
        "event_version": EVENT_VERSION,
        "timestamp": material_signal["created_at"],
        "signal": {
            "signal_id": material_signal["signal_id"],
            "signal_type": material_signal["signal_type"],
            "summary": material_signal["summary"],
            "confidence": material_signal["confidence_label"],
            "materiality": material_signal["materiality_label"],
            "evidence_ids": material_signal["evidence_ids"],
        },
    }


def _append_event(envelope, events_path):
    os.makedirs(os.path.dirname(events_path) or ".", exist_ok=True)
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(envelope) + "\n")


def emit_event(material_signal, events_path=DEFAULT_EVENTS_PATH):
    envelope = build_envelope(material_signal)
    _append_event(envelope, events_path)
    return envelope


def _emitted_signal_ids(events_path):
    if not os.path.exists(events_path):
        return set()
    ids = set()
    with open(events_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                ids.add(json.loads(line)["signal"]["signal_id"])
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return ids


def flush_outbox(conn, events_path=DEFAULT_EVENTS_PATH):
    """Append every pending outbox event to events.jsonl, skipping signal_ids the file already
    has (a crash between appending and marking). Commits per event. Returns how many were written."""
    pending = db.get_pending_outbox(conn)
    if not pending:
        return 0
    already = _emitted_signal_ids(events_path)
    written = 0
    for row in pending:
        if row["signal_id"] not in already:
            _append_event(row["envelope"], events_path)
            written += 1
        db.mark_outbox_emitted(conn, row["outbox_id"])
        conn.commit()
    return written


def run(db_path="data/pi_agent.db", trend_window_weeks=8, today=None, events_path=DEFAULT_EVENTS_PATH):
    run_date = today or date.today()
    week = iso_week_string(run_date)
    conn = db.connect(db_path)
    db.init_db(conn)
    try:
        # Events an earlier run committed but never wrote (it died between the two steps).
        flush_outbox(conn, events_path)

        topics = db.get_canonical_topics(conn)
        for topic in topics:
            topic["weekly_mentions"] = db.get_topic_weekly_mentions(conn, topic["topic_id"])
        trend_by_topic_id = {row["id"]: row for row in report.compute_trends(topics, week, trend_window_weeks)}

        for topic in topics:
            candidates = db.get_candidates_for_topic(conn, topic["topic_id"])
            candidates_this_week = [c for c in candidates if _week_of_iso_timestamp(c["captured_at"]) == week]
            if not candidates_this_week:
                continue

            trend_row = trend_by_topic_id[topic["topic_id"]]
            avg_confidence = sum(c["confidence"] for c in candidates_this_week) / len(candidates_this_week)
            signal_type = _dominant_signal_type(candidates_this_week)
            materiality_result = classify_materiality(
                signal_type=signal_type, trend=trend_row["trend"],
                mentions_this_week=trend_row["mentions_this_week"], avg_confidence=avg_confidence,
            )
            if materiality_result["label"] not in ("high", "critical"):
                continue

            previous = db.get_latest_material_signal_for_topic_week(conn, topic["topic_id"], week)
            if previous and LABEL_RANK[previous["materiality_label"]] >= LABEL_RANK[materiality_result["label"]]:
                continue
            if previous:
                materiality_result = {**materiality_result, "reasons": [
                    *materiality_result["reasons"],
                    f"escalated from {previous['materiality_label']} ({previous['signal_id']}) earlier this week",
                ]}

            material_signal = build_material_signal(
                topic, candidates_this_week, trend_row["trend"], materiality_result, run_date,
            )
            signal_id = db.insert_material_signal(conn, **material_signal, week=week)
            db.enqueue_event(conn, signal_id, build_envelope({**material_signal, "signal_id": signal_id}))
        conn.commit()

        flush_outbox(conn, events_path)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_materiality.py tests/test_end_to_end.py tests/test_db.py -v`
Expected: PASS, including the existing `emit_event` and `run` tests.

- [ ] **Step 6: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add db.py materiality.py tests/test_materiality.py
git commit -m "feat(materiality): one signal per topic and week, events delivered through an outbox"
```

---

### Task 9: Crash-and-resume end-to-end test, health queries, docs

**Files:**
- Create: `tests/test_resume.py`
- Modify: `db.py` (`pipeline_health`, `list_llm_calls`, `llm_usage`)
- Modify: `query.py` (`health`, `calls`, `usage` commands)
- Test: `tests/test_query.py` (append)
- Create: `docs/decisions/005-per-item-durable-llm-calls.md`
- Modify: `README.md`, `CLAUDE.md`

**Interfaces:**
- Consumes: everything from Tasks 3-8.
- Produces:
  - `db.pipeline_health(conn, *, max_match_attempts) -> dict` with keys `evidence_pending_extraction`, `extraction_dead`, `duplicate_evidence`, `candidates_unmatched`, `candidates_stuck`, `llm_calls_pending`, `llm_calls_abandoned`, `match_responses_unapplied`, `events_unflushed`. (P5 adds `review_items_open`.)
  - `db.list_llm_calls(conn, *, stage=None, status=None, limit=20) -> list[dict]` (without `raw_response`)
  - `db.llm_usage(conn, *, since=None) -> list[dict]` with keys `stage`, `model`, `calls`, `input_tokens`, `output_tokens`
  - `query.health_command(conn)`, `query.calls_command(conn, stage, status, limit)`, `query.usage_command(conn, since)`

- [ ] **Step 1: Write the failing end-to-end resume test**

Create `tests/test_resume.py`:

```python
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
```

- [ ] **Step 2: Write the failing query tests**

Append to `tests/test_query.py`:

```python
import ledger
from llm import LLMReply


def test_health_command_reports_pipeline_counts(tmp_path, capsys):
    conn = db.connect(seed_db(tmp_path))

    query.health_command(conn)

    output = json.loads(capsys.readouterr().out)
    assert output["candidates_unmatched"] == 0
    assert set(output) >= {"evidence_pending_extraction", "extraction_dead", "llm_calls_abandoned", "events_unflushed"}


def test_calls_and_usage_commands(tmp_path, capsys):
    conn = db.connect(seed_db(tmp_path))
    call_id = ledger.start_call(conn, stage="extract", subject_key="EV-x", model="claude-sonnet-5", prompt_template="T")
    ledger.finish_call(conn, call_id, status="response_error", error="non-JSON",
                       reply=LLMReply(text="oops", stop_reason="end_turn", input_tokens=100, output_tokens=7))
    conn.commit()

    query.calls_command(conn, stage="extract", status="response_error", limit=5)
    calls = json.loads(capsys.readouterr().out)
    assert [c["error"] for c in calls] == ["non-JSON"]
    assert "raw_response" not in calls[0]

    query.usage_command(conn, since=None)
    usage = json.loads(capsys.readouterr().out)
    assert usage == [{"stage": "extract", "model": "claude-sonnet-5", "calls": 1, "input_tokens": 100, "output_tokens": 7}]
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_resume.py tests/test_query.py -v`
Expected: FAIL (`AttributeError: module 'db' has no attribute 'pipeline_health'`).

- [ ] **Step 4: Add the read helpers to `db.py`**

```python
def pipeline_health(conn, *, max_match_attempts):
    def count(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    return {
        "evidence_pending_extraction": len(get_evidence_pending_extraction(conn)),
        "extraction_dead": count("SELECT COUNT(*) FROM evidence_extraction WHERE status = 'dead'"),
        "duplicate_evidence": count("SELECT COUNT(*) FROM evidence WHERE duplicate_of IS NOT NULL"),
        "candidates_unmatched": count(
            "SELECT COUNT(*) FROM signal_candidate WHERE topic_id IS NULL AND match_attempts < ?", (max_match_attempts,)),
        "candidates_stuck": count(
            "SELECT COUNT(*) FROM signal_candidate WHERE topic_id IS NULL AND match_attempts >= ?", (max_match_attempts,)),
        "llm_calls_pending": count("SELECT COUNT(*) FROM llm_call WHERE status = 'pending'"),
        "llm_calls_abandoned": count("SELECT COUNT(*) FROM llm_call WHERE status = 'abandoned'"),
        "match_responses_unapplied": count(
            "SELECT COUNT(*) FROM llm_call WHERE stage = 'match' AND status = 'succeeded' AND applied_at IS NULL"),
        "events_unflushed": count("SELECT COUNT(*) FROM event_outbox WHERE emitted_at IS NULL"),
    }


def list_llm_calls(conn, *, stage=None, status=None, limit=20):
    sql, params = ("SELECT call_id, stage, subject_key, model, prompt_fingerprint, status, error, stop_reason, "
                   "input_tokens, output_tokens, started_at, finished_at, applied_at FROM llm_call WHERE 1 = 1"), []
    if stage:
        sql, params = sql + " AND stage = ?", [*params, stage]
    if status:
        sql, params = sql + " AND status = ?", [*params, status]
    rows = conn.execute(sql + " ORDER BY call_id DESC LIMIT ?", (*params, limit)).fetchall()
    return [dict(r) for r in rows]


def llm_usage(conn, *, since=None):
    sql, params = ("SELECT stage, model, COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                   "COALESCE(SUM(output_tokens), 0) AS output_tokens FROM llm_call"), ()
    if since:
        sql, params = sql + " WHERE started_at >= ?", (since,)
    rows = conn.execute(sql + " GROUP BY stage, model ORDER BY stage, model", params).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 5: Add the commands to `query.py`**

Add `import match` to the imports, then these three functions:

```python
def health_command(conn):
    print(json.dumps(db.pipeline_health(conn, max_match_attempts=match.MAX_MATCH_ATTEMPTS), indent=2))


def calls_command(conn, stage, status, limit):
    print(json.dumps(db.list_llm_calls(conn, stage=stage, status=status, limit=limit), indent=2))


def usage_command(conn, since):
    print(json.dumps(db.llm_usage(conn, since=since), indent=2))
```

In `main()`, register the subparsers after the `signal` one:

```python
    subparsers.add_parser("health", help="counts of pending, stuck, dead and unflushed work")

    calls_parser = subparsers.add_parser("calls", help="recent LLM calls from the ledger")
    calls_parser.add_argument("--stage")
    calls_parser.add_argument("--status")
    calls_parser.add_argument("--limit", type=int, default=20)

    usage_parser = subparsers.add_parser("usage", help="LLM calls and tokens per stage and model")
    usage_parser.add_argument("--since", help="ISO date, e.g. 2026-10-01")
```

And dispatch them:

```python
    elif args.command == "health":
        health_command(conn)
    elif args.command == "calls":
        calls_command(conn, args.stage, args.status, args.limit)
    elif args.command == "usage":
        usage_command(conn, args.since)
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_resume.py tests/test_query.py -v`
Expected: PASS.

- [ ] **Step 7: Write ADR 005**

Create `docs/decisions/005-per-item-durable-llm-calls.md`:

```markdown
# Decision

Every model call goes through an "effect sandwich" recorded in an `llm_call` ledger:

1. Record the intent and commit.
2. Make the call.
3. Commit the result together with the rows derived from it.

Each stage commits per unit of work (evidence row, matching batch, subreddit, material signal), not once per stage.

## Context

Before this change each stage committed once at the end. A crash threw away every paid LLM call made so far in the run. Skipped and unparseable posts left no record, so they were re-sent, and paid for, on every run. The fetch cursor and the evidence lived in two stores that could disagree. Events were appended after the commit, so a crash in between lost them.

## Options considered

- **Event sourcing** (store every event, rebuild state from it). Rejected: much more machinery than five batch stages need, and every reader would have to change.
- **Retry the whole stage.** Rejected: it is what we had; the cost of a crash grows with the size of the run.
- **Per-item effect sandwich with natural idempotency keys** (chosen): the ledger plus outcome tables (`evidence_extraction`, `match_attempts`, `event_outbox`) plus natural keys (`reddit_post:<id>`, `(topic, week)`).

## Consequences

- Re-running a stage, or the whole pipeline, after a crash converges to the same state (`tests/test_resume.py`).
- The Anthropic API has no idempotency key, so the one call in flight during a crash is repeated. That is the bounded cost of a crash.
- Every candidate and match decision links to the call that produced it (prompt fingerprint, model, raw response, tokens). `python query.py calls` and `usage` read the ledger.
- The ledger grows by one row per model call. No retention policy yet; revisit when the database gets large.
- One pipeline process at a time is now a stated requirement: `abandon_stale_calls` would wrongly abandon another live process's calls.
```

- [ ] **Step 8: Update `README.md` and `CLAUDE.md`**

README changes:

1. **Architecture:**
   - Add `ledger.py` and `llm.py` to the module list. One line each: "records every model call before and after it is made", "reads model responses the same way everywhere".
   - Replace the paragraph that begins "Each stage (`fetch`/`extract`/`match`/`materiality`) opens one DB connection..." in **Observability** with:

   > Each stage commits per unit of work: per subreddit (fetch), per evidence row (extract), per stored response and then per applied batch (match), per material signal together with its outbox event (materiality). A crash at any point loses at most the one LLM call in flight, and re-running converges to the same state (see ADR 005). `python query.py health` shows pending, stuck, dead and unflushed work; `python query.py calls --stage extract --status response_error` lists failed calls; `python query.py usage` totals calls and tokens per stage and model.

2. **Core workflows:** replace step 1 ("fetch.py reads state.json...") with "fetch.py reads the subreddit's cursor from the `fetch_cursor` table (falling back once to a legacy `state.json`)". Add after step 3: "Posts already stored (same Reddit post id) are skipped, so re-fetching is harmless."
3. **AI design decisions table:** add a row: `| Durability | Effect sandwich per model call, recorded in an llm_call ledger | A crash loses at most one call; skips and failures are outcomes, not retries (ADR 005) |`.
4. **Limitations:** add "**One process at a time.** Crash recovery assumes no other pipeline process is running against the same database."

Add to `CLAUDE.md` under `## Who designs what`, first list:

```markdown
- What each stage commits, and when (ADR 005). Model calls go through `ledger.py`; don't call `client.messages.create` from a stage without it.
```

and remove the old line "- What each stage commits, and when." so the item isn't listed twice.

- [ ] **Step 9: Run everything**

Run: `pytest -q && ruff check .`
Expected: all pass.

- [ ] **Step 10: Try the upgrade on a copy of the real database (manual)**

```powershell
Copy-Item data\pi_agent.db data\pi_agent.backup.db
python query.py --db-path data\pi_agent.db health
```

Expected: the command prints the health counts and the database is now at schema version 5 (`python -c "import db; c=db.connect('data/pi_agent.db'); print(db.schema_version(c))"` prints `5`). Check `duplicate_evidence` and say in the PR how many duplicates the backfill found. Keep the backup until the next weekly run succeeds.

- [ ] **Step 11: Commit**

```bash
git add tests/test_resume.py tests/test_query.py db.py query.py docs/decisions/005-per-item-durable-llm-calls.md README.md CLAUDE.md
git commit -m "feat: crash-and-resume test, ledger health queries, ADR 005"
```

---

## Self-review notes

- **Spec coverage:**
  - D1: Task 5 (per-row commit). D2: Task 5 (`skipped` outcome). D3: Task 5 (attempts → `dead`).
  - D4, D5: Task 4 (natural key, cursor in SQLite). D6: Task 8 (outbox, `(topic, week)`). D7: Task 7 (stored response, replay).
  - S1, S2, S3: Task 6 (`resolve_decisions`, slug grouping).
  - P1, P2: Tasks 3, 5, 7 (`llm_call_id`, `match_call_id`, raw responses). X1: Tasks 1 and 9.
  - S4 is P3 Task 2, by design.
- **Names later plans use:** `ledger.start_call` / `finish_call` / `abandon_stale_calls` / `get_unapplied_calls`, `llm.LLMReply`, `db.record_extraction_outcome(..., decided_by=)`, `match.resolve_decisions` (P2 adds `redirects=`), `db.pipeline_health` (P5 adds a key), migrations 1-5.
