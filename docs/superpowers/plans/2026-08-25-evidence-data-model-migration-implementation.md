# Evidence Data Model Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate the existing Reddit pipeline (`fetch.py` → `extract.py` → `match.py` → `report.py`) from flat JSON files (`data/state.json`, `data/registry.json`, `data/raw/`, `data/extracted/`) onto the evidence / signal-candidate / canonical-topic data model, backed by SQLite, so later sub-projects (materiality engine, event contract, additional sources) have a real foundation to build on.

**Architecture:** Same four-stage pipeline shape, same `run_weekly.py` entrypoint, no new dependency (SQLite access via Python's stdlib `sqlite3`). A new `db.py` module owns the schema and all reads/writes to `data/pi_agent.db`; each pipeline stage is modified to call `db.py` instead of `registry.py`/raw JSON files. A new `query.py` gives ad hoc read access, and a one-time `migrate_to_sqlite.py` carries existing registry data into the new tables.

**Tech Stack:** Python 3.11+, stdlib `sqlite3` (no new package), PRAW, Anthropic SDK, PyYAML, `keyring`, pytest.

**Spec:** [docs/superpowers/specs/2026-08-25-evidence-data-model-migration-design.md](../specs/2026-08-25-evidence-data-model-migration-design.md)

## Global Constraints

- SQLite DB lives at `data/pi_agent.db`, accessed only via `sqlite3` (stdlib) — no new entry in `requirements.txt`.
- `evidence` rows are immutable — inserted once by `fetch.py`, never `UPDATE`d afterward.
- Each stage wraps its DB writes in one transaction per invocation: commit only on full success, `conn.rollback()` and re-raise on any unhandled exception, leaving the DB at its pre-run state. This does **not** apply to `extract.py`'s existing single-item skip-on-error behavior — an `ExtractionError` for one evidence row is caught inline, logged, and does not trigger a rollback of the rows already processed in that run.
- `fetch.py` still saves `data/state.json` only after a fully successful run across all configured subreddits (unchanged from before).
- `match.py` / `report.py` / `query.py` fail loudly (raise / non-zero exit) on a missing-but-expected or corrupt DB rather than silently resetting it.
- `canonical_topic` has no `category`/`signal_type` column (per the target spec's section 6 example) — that classification lives on `signal_candidate`, since one topic can accumulate candidates of different signal types over time. `report.py`'s output therefore drops the `category` column it had before the migration.
- `extract.py` processes whichever `evidence` rows have no linked `signal_candidate` yet (a DB query), not files scoped to the current ISO week — this makes re-running the `extract` stage safe/idempotent and is enabled by centralizing evidence in one table.
- ID formats: `evidence_id` = `EV-<year>-<6-digit sequence>`, `candidate_id` = `SC-<year>-<5-digit sequence>` (year taken from the linked evidence row's `captured_at`), `topic_id` = `TOPIC-<4-digit sequence>` (no year component).
- `query.py` is a throwaway ad hoc CLI for development use, not the formal Integration API from the target spec's section 11 — its output shape is not a stable contract for later sub-projects.
- Reddit access, field selection (`id`/`title`/`selftext`/`permalink`/`score`/`num_comments`/`created_utc`, no author/username), and credential handling via `keyring` are unchanged from the existing pipeline.

---

## File Structure

```
db.py                        # NEW: SQLite schema + connection + CRUD helpers for evidence/candidate/topic tables
migrate_to_sqlite.py         # NEW: one-time migration from registry.json into the new DB
query.py                     # NEW: ad hoc read-only CLI against the DB
fetch.py                     # MODIFY: writes evidence rows instead of data/raw/<week>/<subreddit>.json
extract.py                   # MODIFY: signal_type taxonomy + confidence, writes signal_candidate rows
match.py                     # MODIFY: matches candidates against canonical_topic, writes topic_weekly_mentions
report.py                    # MODIFY: trend calc reads canonical_topic/topic_weekly_mentions via db.py
run_weekly.py                # unchanged (calls stage .run() functions using their new defaults)
registry.py                  # DELETE (Task 8) after migrate_to_sqlite.py no longer needs it
tests/
  test_db.py                    # NEW
  test_migrate_to_sqlite.py     # NEW
  test_query.py                 # NEW
  test_fetch.py                 # MODIFY (only the `run()` tests)
  test_extract.py               # MODIFY (whole file — schema fields changed)
  test_match.py                 # MODIFY (whole file — schema fields changed)
  test_report.py                # MODIFY (whole file — schema fields changed)
  test_end_to_end.py            # MODIFY (whole file)
  test_registry.py              # DELETE (Task 8)
  fixtures/
    registry_sample.json        # kept — used by test_migrate_to_sqlite.py
    new_topics_sample.json      # DELETE (Task 4) — no longer used once match.py reads from the DB
```

`db.py` is the only module that issues raw SQL. Every other module (`fetch.py`, `extract.py`, `match.py`, `report.py`, `query.py`, `migrate_to_sqlite.py`) calls `db.py`'s public functions — none of them import `sqlite3` directly.

---

### Task 1: `db.py` — schema, connection, and CRUD helpers

**Files:**
- Create: `db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing (foundational module).
- Produces (used by every task below): `db.DEFAULT_DB_PATH`, `db.DBError`, `db.connect(path=DEFAULT_DB_PATH) -> sqlite3.Connection`, `db.init_db(conn) -> None`, `db.insert_evidence(conn, *, source_type, source_name, source_url, captured_at, published_at, title, content, metadata: dict) -> str`, `db.get_evidence(conn, evidence_id) -> dict | None`, `db.get_evidence_without_candidate(conn) -> list[dict]`, `db.get_evidence_for_topic(conn, topic_id) -> list[dict]`, `db.insert_signal_candidate(conn, *, evidence_id, signal_type, summary, confidence, topic_id=None) -> str`, `db.set_candidate_topic(conn, candidate_id, topic_id) -> None`, `db.get_candidates_without_topic(conn) -> list[dict]`, `db.get_canonical_topics(conn) -> list[dict]`, `db.get_canonical_topic_by_slug(conn, slug) -> dict | None`, `db.insert_canonical_topic(conn, *, slug, name, description, aliases: list[str], first_seen, last_seen) -> str`, `db.update_topic_last_seen(conn, topic_id, last_seen) -> None`, `db.increment_topic_weekly_mentions(conn, topic_id, period, amount=1) -> None`, `db.get_topic_weekly_mentions(conn, topic_id) -> dict[str, int]`, `db.search_evidence_and_candidates(conn, keyword) -> list[dict]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_db.py
import sqlite3

import pytest

import db


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    db.init_db(connection)
    yield connection
    connection.close()


def _insert_sample_evidence(conn, **overrides):
    defaults = dict(
        source_type="reddit_post", source_name="yourproductname",
        source_url="https://reddit.com/r/example/comments/abc",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T10:00:00+00:00",
        title="Please add dark mode", content="Would love a dark theme",
        metadata={"score": 10, "num_comments": 2},
    )
    defaults.update(overrides)
    return db.insert_evidence(conn, **defaults)


def test_init_db_creates_all_tables(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"evidence", "canonical_topic", "signal_candidate", "topic_weekly_mentions"} <= tables


def test_insert_evidence_returns_sequential_ids_scoped_by_year(conn):
    first = _insert_sample_evidence(conn)
    second = _insert_sample_evidence(conn)
    third = _insert_sample_evidence(conn, captured_at="2027-01-01T00:00:00+00:00")

    assert first == "EV-2026-000001"
    assert second == "EV-2026-000002"
    assert third == "EV-2027-000001"


def test_get_evidence_round_trips_metadata_json(conn):
    evidence_id = _insert_sample_evidence(conn, metadata={"score": 42, "num_comments": 7})

    evidence = db.get_evidence(conn, evidence_id)

    assert evidence["metadata"] == {"score": 42, "num_comments": 7}
    assert evidence["title"] == "Please add dark mode"


def test_get_evidence_returns_none_for_unknown_id(conn):
    assert db.get_evidence(conn, "EV-2026-999999") is None


def test_insert_signal_candidate_derives_year_from_linked_evidence(conn):
    evidence_id = _insert_sample_evidence(conn, captured_at="2025-03-01T00:00:00+00:00")

    candidate_id = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand",
        summary="User wants dark mode", confidence=0.9,
    )

    assert candidate_id == "SC-2025-00001"


def test_insert_signal_candidate_rejects_unknown_evidence_id(conn):
    with pytest.raises(db.DBError, match="does not exist"):
        db.insert_signal_candidate(
            conn, evidence_id="EV-2026-000001", signal_type="new_feature_demand",
            summary="orphaned", confidence=0.5,
        )


def test_get_evidence_without_candidate_excludes_processed_rows(conn):
    processed = _insert_sample_evidence(conn)
    pending = _insert_sample_evidence(conn, title="A second post")
    db.insert_signal_candidate(
        conn, evidence_id=processed, signal_type="new_feature_demand", summary="s", confidence=0.8,
    )

    remaining = db.get_evidence_without_candidate(conn)

    assert [e["evidence_id"] for e in remaining] == [pending]


def test_insert_canonical_topic_returns_sequential_ids_without_year(conn):
    first = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )
    second = db.insert_canonical_topic(
        conn, slug="export-to-csv", name="Export to CSV", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )

    assert first == "TOPIC-0001"
    assert second == "TOPIC-0002"


def test_insert_canonical_topic_rejects_duplicate_slug(conn):
    db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )

    with pytest.raises(db.DBError):
        db.insert_canonical_topic(
            conn, slug="dark-mode-support", name="Dark mode support (dup)", description="d",
            aliases=[], first_seen="2026-W31", last_seen="2026-W31",
        )


def test_get_candidates_without_topic_excludes_matched_rows(conn):
    evidence_id = _insert_sample_evidence(conn)
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )
    matched = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand",
        summary="s", confidence=0.8, topic_id=topic_id,
    )
    unmatched = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="usability_issue", summary="s2", confidence=0.7,
    )

    remaining = db.get_candidates_without_topic(conn)

    assert [c["candidate_id"] for c in remaining] == [unmatched]


def test_increment_topic_weekly_mentions_creates_then_accumulates(conn):
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )

    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=1)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=1)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W31", amount=3)

    assert db.get_topic_weekly_mentions(conn, topic_id) == {"2026-W30": 2, "2026-W31": 3}


def test_get_evidence_for_topic_returns_linked_evidence_only(conn):
    linked_evidence = _insert_sample_evidence(conn)
    other_evidence = _insert_sample_evidence(conn, title="unrelated")
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )
    db.insert_signal_candidate(
        conn, evidence_id=linked_evidence, signal_type="new_feature_demand",
        summary="s", confidence=0.9, topic_id=topic_id,
    )
    db.insert_signal_candidate(
        conn, evidence_id=other_evidence, signal_type="new_feature_demand", summary="s2", confidence=0.5,
    )

    linked = db.get_evidence_for_topic(conn, topic_id)

    assert [e["evidence_id"] for e in linked] == [linked_evidence]


def test_search_evidence_and_candidates_matches_title_content_or_summary(conn):
    evidence_id = _insert_sample_evidence(conn, title="Schema browser is slow", content="loading 20k tables")
    db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="usability_issue",
        summary="Users report poor schema loading performance", confidence=0.85,
    )

    by_title = db.search_evidence_and_candidates(conn, "schema browser")
    by_summary = db.search_evidence_and_candidates(conn, "loading performance")
    no_match = db.search_evidence_and_candidates(conn, "pricing")

    assert len(by_title) == 1
    assert len(by_summary) == 1
    assert no_match == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Implement `db.py`**

```python
# db.py
import json
import sqlite3

DEFAULT_DB_PATH = "data/pi_agent.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  published_at TEXT NOT NULL,
  title TEXT,
  content TEXT,
  metadata TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_topic (
  topic_id TEXT PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  aliases TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_candidate (
  candidate_id TEXT PRIMARY KEY,
  evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
  signal_type TEXT NOT NULL,
  topic_id TEXT REFERENCES canonical_topic(topic_id),
  summary TEXT NOT NULL,
  confidence REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS topic_weekly_mentions (
  topic_id TEXT NOT NULL REFERENCES canonical_topic(topic_id),
  period TEXT NOT NULL,
  mentions INTEGER NOT NULL,
  PRIMARY KEY (topic_id, period)
);
"""


class DBError(Exception):
    pass


def connect(path=DEFAULT_DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn):
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def _next_sequence_id(conn, table, id_column, prefix, width):
    cursor = conn.execute(
        f"SELECT {id_column} FROM {table} WHERE {id_column} LIKE ?", (f"{prefix}%",)
    )
    max_seq = 0
    for (existing_id,) in cursor.fetchall():
        max_seq = max(max_seq, int(existing_id[len(prefix):]))
    return f"{prefix}{max_seq + 1:0{width}d}"


def _evidence_row_to_dict(row):
    d = dict(row)
    d["metadata"] = json.loads(d["metadata"])
    return d


def _topic_row_to_dict(row):
    d = dict(row)
    d["aliases"] = json.loads(d["aliases"])
    return d


def insert_evidence(conn, *, source_type, source_name, source_url, captured_at,
                     published_at, title, content, metadata):
    year = captured_at[:4]
    evidence_id = _next_sequence_id(conn, "evidence", "evidence_id", f"EV-{year}-", 6)
    conn.execute(
        "INSERT INTO evidence (evidence_id, source_type, source_name, source_url, "
        "captured_at, published_at, title, content, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (evidence_id, source_type, source_name, source_url, captured_at, published_at,
         title, content, json.dumps(metadata, sort_keys=True)),
    )
    return evidence_id


def get_evidence(conn, evidence_id):
    row = conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
    return _evidence_row_to_dict(row) if row else None


def get_evidence_without_candidate(conn):
    rows = conn.execute(
        "SELECT e.* FROM evidence e LEFT JOIN signal_candidate sc ON sc.evidence_id = e.evidence_id "
        "WHERE sc.candidate_id IS NULL ORDER BY e.evidence_id"
    ).fetchall()
    return [_evidence_row_to_dict(r) for r in rows]


def get_evidence_for_topic(conn, topic_id):
    rows = conn.execute(
        "SELECT e.* FROM evidence e JOIN signal_candidate sc ON sc.evidence_id = e.evidence_id "
        "WHERE sc.topic_id = ? ORDER BY e.evidence_id",
        (topic_id,),
    ).fetchall()
    return [_evidence_row_to_dict(r) for r in rows]


def insert_signal_candidate(conn, *, evidence_id, signal_type, summary, confidence, topic_id=None):
    evidence = get_evidence(conn, evidence_id)
    if evidence is None:
        raise DBError(f"cannot create signal_candidate: evidence {evidence_id} does not exist")
    year = evidence["captured_at"][:4]
    candidate_id = _next_sequence_id(conn, "signal_candidate", "candidate_id", f"SC-{year}-", 5)
    conn.execute(
        "INSERT INTO signal_candidate (candidate_id, evidence_id, signal_type, topic_id, summary, confidence) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (candidate_id, evidence_id, signal_type, topic_id, summary, confidence),
    )
    return candidate_id


def set_candidate_topic(conn, candidate_id, topic_id):
    conn.execute("UPDATE signal_candidate SET topic_id = ? WHERE candidate_id = ?", (topic_id, candidate_id))


def get_candidates_without_topic(conn):
    rows = conn.execute(
        "SELECT * FROM signal_candidate WHERE topic_id IS NULL ORDER BY candidate_id"
    ).fetchall()
    return [dict(r) for r in rows]


def get_canonical_topics(conn):
    rows = conn.execute("SELECT * FROM canonical_topic ORDER BY topic_id").fetchall()
    return [_topic_row_to_dict(r) for r in rows]


def get_canonical_topic_by_slug(conn, slug):
    row = conn.execute("SELECT * FROM canonical_topic WHERE slug = ?", (slug,)).fetchone()
    return _topic_row_to_dict(row) if row else None


def insert_canonical_topic(conn, *, slug, name, description, aliases, first_seen, last_seen):
    topic_id = _next_sequence_id(conn, "canonical_topic", "topic_id", "TOPIC-", 4)
    try:
        conn.execute(
            "INSERT INTO canonical_topic (topic_id, slug, name, description, aliases, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (topic_id, slug, name, description, json.dumps(aliases), first_seen, last_seen),
        )
    except sqlite3.IntegrityError as e:
        raise DBError(f"cannot create canonical_topic with slug {slug!r}: {e}") from e
    return topic_id


def update_topic_last_seen(conn, topic_id, last_seen):
    conn.execute("UPDATE canonical_topic SET last_seen = ? WHERE topic_id = ?", (last_seen, topic_id))


def increment_topic_weekly_mentions(conn, topic_id, period, amount=1):
    conn.execute(
        "INSERT INTO topic_weekly_mentions (topic_id, period, mentions) VALUES (?, ?, ?) "
        "ON CONFLICT(topic_id, period) DO UPDATE SET mentions = mentions + excluded.mentions",
        (topic_id, period, amount),
    )


def get_topic_weekly_mentions(conn, topic_id):
    rows = conn.execute(
        "SELECT period, mentions FROM topic_weekly_mentions WHERE topic_id = ?", (topic_id,)
    ).fetchall()
    return {row["period"]: row["mentions"] for row in rows}


def search_evidence_and_candidates(conn, keyword):
    like = f"%{keyword}%"
    rows = conn.execute(
        "SELECT sc.candidate_id, sc.summary, sc.signal_type, e.evidence_id, e.title, e.source_url "
        "FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id "
        "WHERE e.title LIKE ? OR e.content LIKE ? OR sc.summary LIKE ?",
        (like, like, like),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (16 tests)

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: add db.py with SQLite schema for evidence/candidate/topic model"
```

---

### Task 2: Migrate `fetch.py` to write `evidence` rows

**Files:**
- Modify: `fetch.py`
- Modify: `tests/test_fetch.py` (only the `run()` tests — `fetch_new_posts`/error-wrapping tests are unaffected)

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db.insert_evidence` (Task 1).
- Produces: `fetch.run(subreddits, fetch_limit_per_subreddit, state_path="data/state.json", db_path="data/pi_agent.db", today=None, reddit_client=None) -> None` — the `raw_dir` parameter is removed. `fetch.fetch_new_posts` and `fetch.build_reddit_client` are unchanged. Used by `run_weekly.py` and `tests/test_end_to_end.py`.

- [ ] **Step 1: Write the failing tests**

Replace the two `run()`-related tests at the bottom of `tests/test_fetch.py` (`test_run_writes_raw_json_and_updates_state` and `test_run_leaves_state_untouched_when_a_subreddit_fetch_fails`) with:

```python
# tests/test_fetch.py — add near the top, alongside the other imports
import db


# tests/test_fetch.py — replace the two run() tests at the bottom of the file
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetch.py -v`
Expected: FAIL — `fetch.run()` still writes `data/raw/...` and takes a `raw_dir` argument, not `db_path`

- [ ] **Step 3: Implement the changes in `fetch.py`**

```python
# fetch.py — replace the `import json`/`import os` block and the run() function
from datetime import date, datetime, timezone

import praw
import prawcore

import db
from credentials import get_secret
from state import load_state, save_state

# (keep REMOVED_MARKERS, FetchError, _wrap_prawcore_error, build_reddit_client,
#  _is_removed, _post_to_dict, fetch_new_posts exactly as they are today)


def run(subreddits, fetch_limit_per_subreddit, state_path="data/state.json",
        db_path="data/pi_agent.db", today=None, reddit_client=None):
    reddit_client = reddit_client or build_reddit_client()
    state = load_state(state_path)
    run_date = today or date.today()
    captured_at = datetime(run_date.year, run_date.month, run_date.day, tzinfo=timezone.utc).isoformat()

    conn = db.connect(db_path)
    db.init_db(conn)

    new_state = dict(state)
    try:
        for subreddit_name in subreddits:
            last_seen = state.get(subreddit_name)
            posts, newest = fetch_new_posts(reddit_client, subreddit_name, last_seen, fetch_limit_per_subreddit)

            for post in posts:
                db.insert_evidence(
                    conn,
                    source_type="reddit_post",
                    source_name=subreddit_name,
                    source_url=post["permalink"],
                    captured_at=captured_at,
                    published_at=datetime.fromtimestamp(post["created_utc"], tz=timezone.utc).isoformat(),
                    title=post["title"],
                    content=post["selftext"],
                    metadata={"reddit_post_id": post["id"], "score": post["score"], "num_comments": post["num_comments"]},
                )

            new_state[subreddit_name] = newest
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()
    save_state(state_path, new_state)


if __name__ == "__main__":
    run(subreddits=[], fetch_limit_per_subreddit=100)
```

Note: `weekutil.iso_week_string` and the `os`/`json` imports are no longer used in `fetch.py` — remove them.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_fetch.py -v`
Expected: PASS (all tests, including the unchanged `fetch_new_posts`/error-wrapping ones)

- [ ] **Step 5: Commit**

```bash
git add fetch.py tests/test_fetch.py
git commit -m "feat: fetch.py writes evidence rows to SQLite instead of raw JSON"
```

---

### Task 3: Migrate `extract.py` to the signal-type taxonomy and `signal_candidate` rows

**Files:**
- Modify: `extract.py`
- Modify: `tests/test_extract.py` (whole file — schema fields changed)

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db.get_evidence_without_candidate`, `db.insert_signal_candidate` (Task 1).
- Produces: `extract.SIGNAL_TYPES: set[str]`, `extract.build_extraction_prompt(evidence: dict) -> str`, `extract.extract_topic(client, evidence: dict) -> dict | None` (raises `extract.ExtractionError`; return shape `{"signal_type", "summary", "confidence"}`), `extract.run(db_path="data/pi_agent.db", today=None, client=None) -> None`. The `raw_dir`/`out_dir` parameters are removed. Used by `run_weekly.py` and `tests/test_end_to_end.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_extract.py
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
    bad_id = db.insert_evidence(
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_extract.py -v`
Expected: FAIL — `extract.py` still uses `VALID_CATEGORIES`/`category` and file-based `run()`

- [ ] **Step 3: Implement the changes in `extract.py`**

```python
# extract.py
import json

import anthropic

import db
from credentials import get_secret

SIGNAL_TYPES = {
    "complaint_rising",
    "complaint_falling",
    "new_feature_demand",
    "new_use_case",
    "usability_issue",
    "reliability_issue",
    "pricing_complaint",
    "switching_intent",
    "competitor_mention_rising",
    "customer_migration_intent",
    "churn_related_issue",
    "positive_adoption_pattern",
}

EXTRACTION_MODEL = "claude-sonnet-5"

EXTRACTION_PROMPT_TEMPLATE = """You are analyzing a Reddit post about a software product for product \
feedback signal extraction.

Post title: {title}
Post body: {content}

Respond with ONLY a JSON object with these exact keys:
- "signal_type": one of {signal_types}
- "summary": a one-line description of what's being said
- "confidence": a number between 0.0 and 1.0 for how confident you are in this classification

If the post is not meaningful product feedback (spam, off-topic, low-effort, or clearly \
AI-generated filler), respond with exactly: {{"skip": true}}
"""


class ExtractionError(Exception):
    pass


def build_extraction_prompt(evidence: dict) -> str:
    return EXTRACTION_PROMPT_TEMPLATE.format(
        title=evidence["title"], content=evidence["content"],
        signal_types=", ".join(f'"{t}"' for t in sorted(SIGNAL_TYPES)),
    )


def extract_topic(client, evidence: dict):
    prompt = build_extraction_prompt(evidence)

    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = response.content[0].text
    except Exception as e:
        raise ExtractionError(f"evidence {evidence['evidence_id']}: API call failed: {e}") from e

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ExtractionError(f"evidence {evidence['evidence_id']}: non-JSON response: {raw_text!r}") from e

    if parsed.get("skip"):
        return None

    missing = {"signal_type", "summary", "confidence"} - parsed.keys()
    if missing:
        raise ExtractionError(f"evidence {evidence['evidence_id']}: response missing keys {missing}")

    if parsed["signal_type"] not in SIGNAL_TYPES:
        raise ExtractionError(f"evidence {evidence['evidence_id']}: invalid signal_type {parsed['signal_type']!r}")

    return {"signal_type": parsed["signal_type"], "summary": parsed["summary"], "confidence": parsed["confidence"]}


def run(db_path="data/pi_agent.db", today=None, client=None):
    client = client or anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    conn = db.connect(db_path)
    db.init_db(conn)

    pending = db.get_evidence_without_candidate(conn)
    try:
        for evidence in pending:
            try:
                result = extract_topic(client, evidence)
            except ExtractionError as e:
                print(f"skipping evidence due to extraction error: {e}")
                continue
            if result is not None:
                db.insert_signal_candidate(
                    conn,
                    evidence_id=evidence["evidence_id"],
                    signal_type=result["signal_type"],
                    summary=result["summary"],
                    confidence=result["confidence"],
                )
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_extract.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add extract.py tests/test_extract.py
git commit -m "feat: extract.py classifies into the full signal_type taxonomy and writes signal_candidate rows"
```

---

### Task 4: Migrate `match.py` to `canonical_topic` / `topic_weekly_mentions`

**Files:**
- Modify: `match.py`
- Modify: `tests/test_match.py` (whole file — schema fields changed)
- Delete: `tests/fixtures/new_topics_sample.json` (no longer used — `match.py` reads candidates from the DB)

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db.get_candidates_without_topic`, `db.get_canonical_topics`, `db.insert_canonical_topic`, `db.update_topic_last_seen`, `db.set_candidate_topic`, `db.increment_topic_weekly_mentions` (Task 1).
- Produces: `match.build_matching_prompt(candidates: list[dict], existing_topics: list[dict]) -> str`, `match.apply_matches(conn, candidates: list[dict], decisions: list[dict], week: str) -> None`, `match.run(db_path="data/pi_agent.db", today=None, client=None) -> None`. `matched_id`/`id_factory` are removed — matching decisions now use the `matched_topic_id: str | None` / `new_topic: {"name", "slug", "description"} | None` shape below. Used by `run_weekly.py` and `tests/test_end_to_end.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_match.py
import db
import match


def seed_db(tmp_path):
    db_path = tmp_path / "pi_agent.db"
    conn = db.connect(str(db_path))
    db.init_db(conn)
    return conn


def seed_existing_topic(conn):
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support",
        description="Users requesting a dark theme option", aliases=[],
        first_seen="2026-W30", last_seen="2026-W31",
    )
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=2)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W31", amount=3)
    conn.commit()
    return topic_id


def seed_candidate(conn, summary, signal_type="new_feature_demand"):
    evidence_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="/x",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="t", content="c", metadata={},
    )
    candidate_id = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type=signal_type, summary=summary, confidence=0.9,
    )
    conn.commit()
    return candidate_id


def test_apply_matches_increments_existing_topic_on_match(tmp_path):
    conn = seed_db(tmp_path)
    topic_id = seed_existing_topic(conn)
    candidate_id = seed_candidate(conn, "Another dark mode request")

    candidates = db.get_candidates_without_topic(conn)
    decisions = [{"index": 0, "matched_topic_id": topic_id, "new_topic": None}]

    match.apply_matches(conn, candidates, decisions, week="2026-W33")
    conn.commit()

    assert db.get_topic_weekly_mentions(conn, topic_id)["2026-W33"] == 1
    updated_candidates = conn.execute(
        "SELECT topic_id FROM signal_candidate WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    assert updated_candidates["topic_id"] == topic_id


def test_apply_matches_creates_new_topic_on_no_match(tmp_path):
    conn = seed_db(tmp_path)
    candidate_id = seed_candidate(conn, "User wants CSV export")

    candidates = db.get_candidates_without_topic(conn)
    decisions = [{
        "index": 0, "matched_topic_id": None,
        "new_topic": {"name": "Export to CSV", "slug": "export-to-csv", "description": "User wants CSV export"},
    }]

    match.apply_matches(conn, candidates, decisions, week="2026-W33")
    conn.commit()

    topic = db.get_canonical_topic_by_slug(conn, "export-to-csv")
    assert topic["name"] == "Export to CSV"
    assert topic["first_seen"] == "2026-W33"
    assert db.get_topic_weekly_mentions(conn, topic["topic_id"]) == {"2026-W33": 1}


def test_apply_matches_does_nothing_when_no_candidates(tmp_path):
    conn = seed_db(tmp_path)

    match.apply_matches(conn, [], [], week="2026-W33")
    conn.commit()

    assert db.get_canonical_topics(conn) == []


def test_build_matching_prompt_includes_topic_and_candidate_text(tmp_path):
    conn = seed_db(tmp_path)
    seed_existing_topic(conn)
    seed_candidate(conn, "User wants CSV export")

    existing = db.get_canonical_topics(conn)
    candidates = db.get_candidates_without_topic(conn)

    prompt = match.build_matching_prompt(candidates, existing)

    assert "Dark mode support" in prompt
    assert "User wants CSV export" in prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_match.py -v`
Expected: FAIL — `match.py` still imports `registry` and uses `apply_matches(registry, new_topics, decisions, week, id_factory)`

- [ ] **Step 3: Implement the changes in `match.py`**

```python
# match.py
import json
from datetime import date

import anthropic

import db
from credentials import get_secret
from weekutil import iso_week_string

MATCH_MODEL = "claude-sonnet-5"

MATCHING_PROMPT_TEMPLATE = """You are matching newly observed product feedback signals against a \
canonical registry of existing topics for the same product.

Existing canonical topics:
{existing_topics_json}

New signal candidates observed this week:
{candidates_json}

For each candidate (by its "index"), decide whether it is semantically the same as one of the \
existing canonical topics, or represents a genuinely new topic.

Respond with ONLY a JSON array, one object per candidate, in the same order as given:
[
  {{"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": null}},
  {{"index": 1, "matched_topic_id": null, "new_topic": {{"name": "Export to CSV", "slug": "export-to-csv", "description": "User wants CSV export"}}}}
]

Use "matched_topic_id": null and fill in "new_topic" only when the candidate is a genuinely new topic, \
not just the same signal_type as an existing one. Slugs must be lowercase, hyphen-separated, and unique.
"""


class MatchError(Exception):
    pass


def build_matching_prompt(candidates, existing_topics):
    existing_summary = [
        {"topic_id": t["topic_id"], "name": t["name"], "description": t["description"]}
        for t in existing_topics
    ]
    candidate_summary = [
        {"index": i, "signal_type": c["signal_type"], "summary": c["summary"]}
        for i, c in enumerate(candidates)
    ]
    return MATCHING_PROMPT_TEMPLATE.format(
        existing_topics_json=json.dumps(existing_summary, indent=2),
        candidates_json=json.dumps(candidate_summary, indent=2),
    )


def _call_matcher(client, candidates, existing_topics):
    if not candidates:
        return []

    prompt = build_matching_prompt(candidates, existing_topics)
    try:
        response = client.messages.create(
            model=MATCH_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(response.content[0].text)
    except Exception as e:
        raise MatchError(f"matching call failed: {e}") from e


def apply_matches(conn, candidates, decisions, week):
    decisions_by_index = {d["index"]: d for d in decisions}

    for i, candidate in enumerate(candidates):
        decision = decisions_by_index.get(i, {})
        matched_topic_id = decision.get("matched_topic_id")

        if matched_topic_id:
            topic_id = matched_topic_id
            db.update_topic_last_seen(conn, topic_id, week)
        else:
            new_topic = decision["new_topic"]
            topic_id = db.insert_canonical_topic(
                conn,
                slug=new_topic["slug"],
                name=new_topic["name"],
                description=new_topic["description"],
                aliases=[],
                first_seen=week,
                last_seen=week,
            )

        db.set_candidate_topic(conn, candidate["candidate_id"], topic_id)
        db.increment_topic_weekly_mentions(conn, topic_id, week, amount=1)


def run(db_path="data/pi_agent.db", today=None, client=None):
    week = iso_week_string(today or date.today())
    conn = db.connect(db_path)
    db.init_db(conn)

    client = client or anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    try:
        candidates = db.get_candidates_without_topic(conn)
        existing_topics = db.get_canonical_topics(conn)
        decisions = _call_matcher(client, candidates, existing_topics)
        apply_matches(conn, candidates, decisions, week)
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_match.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Delete the now-unused fixture and commit**

```bash
git rm tests/fixtures/new_topics_sample.json
git add match.py tests/test_match.py
git commit -m "feat: match.py matches signal candidates against canonical_topic in SQLite"
```

---

### Task 5: Migrate `report.py` to SQL-backed trend computation

**Files:**
- Modify: `report.py`
- Modify: `tests/test_report.py` (whole file — schema fields changed, `category` column dropped)

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db.get_canonical_topics`, `db.get_topic_weekly_mentions` (Task 1).
- Produces: `report.compute_trends(topics: list[dict], current_week: str, trend_window_weeks: int) -> list[dict]` (each `topic` dict must include a `weekly_mentions: dict[str, int]` key alongside its `canonical_topic` fields), `report.write_report(rows, json_path, csv_path) -> None`, `report.run(db_path="data/pi_agent.db", report_dir="data/reports", trend_window_weeks=8, today=None) -> None`. `REPORT_FIELDNAMES` drops `"category"`: `["id", "canonical_name", "mentions_this_week", "total_mentions", "trend"]`. Used by `run_weekly.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_report.py
import csv
import json

import report


def make_topic(topic_id, name, first_seen, weekly_mentions):
    return {"topic_id": topic_id, "name": name, "first_seen": first_seen, "weekly_mentions": weekly_mentions}


def test_compute_trends_marks_first_seen_this_week_as_new():
    topics = [make_topic("TOPIC-0001", "New topic", "2026-W33", {"2026-W33": 1})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "new"
    assert rows[0]["mentions_this_week"] == 1


def test_compute_trends_marks_rising_when_above_recent_average():
    topics = [make_topic("TOPIC-0001", "Dark mode", "2026-W30",
                          {"2026-W30": 2, "2026-W31": 2, "2026-W32": 2, "2026-W33": 10})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "rising"


def test_compute_trends_marks_falling_when_below_recent_average():
    topics = [make_topic("TOPIC-0001", "Dark mode", "2026-W30",
                          {"2026-W30": 10, "2026-W31": 10, "2026-W32": 10, "2026-W33": 1})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "falling"


def test_compute_trends_marks_stable_within_band():
    topics = [make_topic("TOPIC-0001", "Dark mode", "2026-W30",
                          {"2026-W30": 5, "2026-W31": 5, "2026-W32": 5, "2026-W33": 5})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "stable"


def test_compute_trends_weights_recent_weeks_more_heavily():
    # Same math as before the migration: flat average would call this "falling",
    # the recency-weighted average keeps it "stable".
    topics = [make_topic("TOPIC-0001", "Dark mode", "2026-W29",
                          {"2026-W30": 10, "2026-W31": 3, "2026-W32": 1, "2026-W33": 3})]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert rows[0]["trend"] == "stable"


def test_compute_trends_sorts_by_mentions_this_week_descending():
    topics = [
        make_topic("TOPIC-LOW", "Low", "2026-W33", {"2026-W33": 1}),
        make_topic("TOPIC-HIGH", "High", "2026-W33", {"2026-W33": 9}),
    ]

    rows = report.compute_trends(topics, "2026-W33", trend_window_weeks=8)

    assert [r["id"] for r in rows] == ["TOPIC-HIGH", "TOPIC-LOW"]


def test_write_report_produces_matching_json_and_csv(tmp_path):
    rows = [{"id": "TOPIC-0001", "canonical_name": "Dark mode", "mentions_this_week": 5,
             "total_mentions": 12, "trend": "rising"}]
    json_path = tmp_path / "reports" / "2026-W33.json"
    csv_path = tmp_path / "reports" / "2026-W33.csv"

    report.write_report(rows, str(json_path), str(csv_path))

    with open(json_path, "r", encoding="utf-8") as f:
        assert json.load(f) == rows

    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = list(csv.DictReader(f))
    assert reader[0]["canonical_name"] == "Dark mode"
    assert reader[0]["trend"] == "rising"
    assert "category" not in reader[0]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_report.py -v`
Expected: FAIL — `report.compute_trends` still expects a `{"topics": [...]}` registry dict with `"canonical_name"`/`"category"`/`"first_seen_week"` keys

- [ ] **Step 3: Implement the changes in `report.py`**

```python
# report.py
import csv
import json
import os
from datetime import date

import db
from weekutil import iso_week_string

TREND_NEW = "new"
TREND_RISING = "rising"
TREND_STABLE = "stable"
TREND_FALLING = "falling"

RISING_THRESHOLD = 1.2
FALLING_THRESHOLD = 0.8

REPORT_FIELDNAMES = ["id", "canonical_name", "mentions_this_week", "total_mentions", "trend"]


def _recent_average(weekly_mentions, current_week, trend_window_weeks):
    past_weeks = sorted(w for w in weekly_mentions if w < current_week)
    recent_weeks = past_weeks[-trend_window_weeks:]
    if not recent_weeks:
        return 0.0
    weighted_sum = sum((i + 1) * weekly_mentions[w] for i, w in enumerate(recent_weeks))
    weight_total = sum(range(1, len(recent_weeks) + 1))
    return weighted_sum / weight_total


def _trend_direction(mentions_this_week, recent_average, first_seen_week, current_week):
    if first_seen_week == current_week:
        return TREND_NEW
    if recent_average == 0:
        return TREND_RISING if mentions_this_week > 0 else TREND_STABLE
    if mentions_this_week > recent_average * RISING_THRESHOLD:
        return TREND_RISING
    if mentions_this_week < recent_average * FALLING_THRESHOLD:
        return TREND_FALLING
    return TREND_STABLE


def compute_trends(topics, current_week, trend_window_weeks):
    rows = []
    for topic in topics:
        weekly = topic["weekly_mentions"]
        mentions_this_week = weekly.get(current_week, 0)
        recent_average = _recent_average(weekly, current_week, trend_window_weeks)

        rows.append({
            "id": topic["topic_id"],
            "canonical_name": topic["name"],
            "mentions_this_week": mentions_this_week,
            "total_mentions": sum(weekly.values()),
            "trend": _trend_direction(mentions_this_week, recent_average, topic["first_seen"], current_week),
        })

    rows.sort(key=lambda r: r["mentions_this_week"], reverse=True)
    return rows


def write_report(rows, json_path, csv_path):
    os.makedirs(os.path.dirname(json_path) or ".", exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2)

    os.makedirs(os.path.dirname(csv_path) or ".", exist_ok=True)
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=REPORT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def run(db_path="data/pi_agent.db", report_dir="data/reports", trend_window_weeks=8, today=None):
    week = iso_week_string(today or date.today())
    conn = db.connect(db_path)
    db.init_db(conn)
    topics = db.get_canonical_topics(conn)
    for topic in topics:
        topic["weekly_mentions"] = db.get_topic_weekly_mentions(conn, topic["topic_id"])
    conn.close()

    rows = compute_trends(topics, week, trend_window_weeks)
    write_report(rows, os.path.join(report_dir, f"{week}.json"), os.path.join(report_dir, f"{week}.csv"))


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_report.py -v`
Expected: PASS (7 tests)

- [ ] **Step 5: Commit**

```bash
git add report.py tests/test_report.py
git commit -m "feat: report.py computes trends from canonical_topic/topic_weekly_mentions in SQLite"
```

---

### Task 6: `query.py` — ad hoc read-only CLI

**Files:**
- Create: `query.py`
- Test: `tests/test_query.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db.get_canonical_topic_by_slug`, `db.get_topic_weekly_mentions`, `db.get_evidence_for_topic`, `db.search_evidence_and_candidates` (Task 1).
- Produces: `query.topic_command(conn, slug) -> None` (prints JSON), `query.search_command(conn, keyword) -> None` (prints JSON), `query.main() -> None` (CLI: `topic <slug>` / `search <keyword>`, both accepting `--db-path`). Standalone tool — no other module imports `query.py`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_query.py
import json
import sys

import db
import query


def seed_db(tmp_path):
    db_path = tmp_path / "pi_agent.db"
    conn = db.connect(str(db_path))
    db.init_db(conn)
    evidence_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="https://reddit.com/x",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="Schema browser is slow", content="loading 20k tables takes forever", metadata={},
    )
    topic_id = db.insert_canonical_topic(
        conn, slug="slow-schema-loading", name="Slow schema loading",
        description="Users report poor performance", aliases=[],
        first_seen="2026-W30", last_seen="2026-W33",
    )
    db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="usability_issue",
        summary="Users report poor schema loading performance", confidence=0.9, topic_id=topic_id,
    )
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W33", amount=1)
    conn.commit()
    conn.close()
    return str(db_path)


def test_topic_command_prints_trend_and_evidence(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    conn = db.connect(db_path)

    query.topic_command(conn, "slow-schema-loading")

    conn.close()
    output = json.loads(capsys.readouterr().out)
    assert output["topic"]["name"] == "Slow schema loading"
    assert output["weekly_mentions"] == {"2026-W33": 1}
    assert output["evidence_urls"] == ["https://reddit.com/x"]


def test_topic_command_reports_unknown_slug(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    conn = db.connect(db_path)

    query.topic_command(conn, "does-not-exist")

    conn.close()
    assert "no topic found" in capsys.readouterr().out


def test_search_command_matches_keyword(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    conn = db.connect(db_path)

    query.search_command(conn, "schema loading")

    conn.close()
    output = json.loads(capsys.readouterr().out)
    assert len(output) == 1
    assert output[0]["signal_type"] == "usability_issue"


def test_main_topic_subcommand(tmp_path, monkeypatch, capsys):
    db_path = seed_db(tmp_path)
    monkeypatch.setattr(sys, "argv", ["query.py", "--db-path", db_path, "topic", "slow-schema-loading"])

    query.main()

    output = json.loads(capsys.readouterr().out)
    assert output["topic"]["slug"] == "slow-schema-loading"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_query.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'query'`

- [ ] **Step 3: Implement `query.py`**

```python
# query.py
import argparse
import json

import db


def topic_command(conn, slug):
    topic = db.get_canonical_topic_by_slug(conn, slug)
    if topic is None:
        print(f"no topic found with slug {slug!r}")
        return

    weekly_mentions = db.get_topic_weekly_mentions(conn, topic["topic_id"])
    evidence = db.get_evidence_for_topic(conn, topic["topic_id"])
    print(json.dumps({
        "topic": topic,
        "weekly_mentions": weekly_mentions,
        "evidence_urls": [e["source_url"] for e in evidence],
    }, indent=2))


def search_command(conn, keyword):
    results = db.search_evidence_and_candidates(conn, keyword)
    print(json.dumps(results, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Ad hoc read-only queries against the product intelligence DB")
    parser.add_argument("--db-path", default="data/pi_agent.db")
    subparsers = parser.add_subparsers(dest="command", required=True)

    topic_parser = subparsers.add_parser("topic")
    topic_parser.add_argument("slug")

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("keyword")

    args = parser.parse_args()
    conn = db.connect(args.db_path)
    db.init_db(conn)

    if args.command == "topic":
        topic_command(conn, args.slug)
    elif args.command == "search":
        search_command(conn, args.keyword)

    conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_query.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add query.py tests/test_query.py
git commit -m "feat: add query.py ad hoc read CLI for the product intelligence DB"
```

---

### Task 7: `migrate_to_sqlite.py` — one-time migration from `registry.json`

**Files:**
- Create: `migrate_to_sqlite.py`
- Test: `tests/test_migrate_to_sqlite.py`
- Keep: `tests/fixtures/registry_sample.json` (used by this test)

**Interfaces:**
- Consumes: `registry.load_registry` (still present at this point — deleted in Task 8), `db.connect`, `db.init_db`, `db.insert_canonical_topic`, `db.increment_topic_weekly_mentions`, `db.insert_evidence`, `db.insert_signal_candidate` (Task 1).
- Produces: `migrate_to_sqlite.LEGACY_CATEGORY_TO_SIGNAL_TYPE: dict[str, str]`, `migrate_to_sqlite.migrate(registry_path, db_path) -> None`, `migrate_to_sqlite.main() -> None` (CLI with `--registry-path`/`--db-path`). Run once by hand during the cutover (Task 8) — no other module imports it.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_migrate_to_sqlite.py
from pathlib import Path

import db
import migrate_to_sqlite

FIXTURES = Path(__file__).parent / "fixtures"


def test_migrate_creates_topic_with_weekly_mentions_and_placeholder_evidence(tmp_path):
    db_path = tmp_path / "pi_agent.db"

    migrate_to_sqlite.migrate(str(FIXTURES / "registry_sample.json"), str(db_path))

    conn = db.connect(str(db_path))
    topic = db.get_canonical_topic_by_slug(conn, "dark-mode-support")
    assert topic["name"] == "Dark mode support"
    assert topic["first_seen"] == "2026-W30"
    assert topic["last_seen"] == "2026-W31"

    mentions = db.get_topic_weekly_mentions(conn, topic["topic_id"])
    assert mentions == {"2026-W30": 2, "2026-W31": 3}

    evidence = db.get_evidence_for_topic(conn, topic["topic_id"])
    assert evidence[0]["source_url"] == "https://reddit.com/r/example/comments/abc"
    assert evidence[0]["metadata"] == {"migrated": True}
    conn.close()


def test_migrate_maps_legacy_category_to_signal_type(tmp_path):
    db_path = tmp_path / "pi_agent.db"

    migrate_to_sqlite.migrate(str(FIXTURES / "registry_sample.json"), str(db_path))

    conn = db.connect(str(db_path))
    topic = db.get_canonical_topic_by_slug(conn, "dark-mode-support")
    candidate = conn.execute(
        "SELECT signal_type FROM signal_candidate WHERE topic_id = ?", (topic["topic_id"],)
    ).fetchone()
    conn.close()

    assert candidate["signal_type"] == "new_feature_demand"  # fixture's category is "feature_request"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_migrate_to_sqlite.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'migrate_to_sqlite'`

- [ ] **Step 3: Implement `migrate_to_sqlite.py`**

```python
# migrate_to_sqlite.py
import argparse

import db
from registry import load_registry

LEGACY_CATEGORY_TO_SIGNAL_TYPE = {
    "feature_request": "new_feature_demand",
    "complaint": "complaint_rising",
    "praise": "positive_adoption_pattern",
    "question": "usability_issue",
    "competitor_comparison": "competitor_mention_rising",
}


def _slugify(name):
    return "-".join(name.lower().split())


def migrate(registry_path, db_path):
    registry = load_registry(registry_path)
    conn = db.connect(db_path)
    db.init_db(conn)

    try:
        for topic in registry["topics"]:
            weeks = sorted(topic["weekly_mentions"])
            first_seen = topic.get("first_seen_week", weeks[0] if weeks else "")
            last_seen = weeks[-1] if weeks else first_seen
            signal_type = LEGACY_CATEGORY_TO_SIGNAL_TYPE.get(topic.get("category"), "new_feature_demand")

            topic_id = db.insert_canonical_topic(
                conn,
                slug=_slugify(topic["canonical_name"]),
                name=topic["canonical_name"],
                description=topic["description"],
                aliases=[],
                first_seen=first_seen,
                last_seen=last_seen,
            )

            for week, mentions in topic["weekly_mentions"].items():
                db.increment_topic_weekly_mentions(conn, topic_id, week, amount=mentions)

            for permalink in topic["example_permalinks"]:
                evidence_id = db.insert_evidence(
                    conn,
                    source_type="reddit_post",
                    source_name="migrated",
                    source_url=permalink,
                    captured_at=f"{last_seen[:4]}-01-01T00:00:00+00:00",
                    published_at=f"{last_seen[:4]}-01-01T00:00:00+00:00",
                    title=None,
                    content=None,
                    metadata={"migrated": True},
                )
                db.insert_signal_candidate(
                    conn,
                    evidence_id=evidence_id,
                    signal_type=signal_type,
                    summary=f"Migrated from legacy registry topic {topic['canonical_name']!r}",
                    confidence=1.0,
                    topic_id=topic_id,
                )
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="One-time migration from registry.json to SQLite")
    parser.add_argument("--registry-path", default="data/registry.json")
    parser.add_argument("--db-path", default="data/pi_agent.db")
    args = parser.parse_args()
    migrate(args.registry_path, args.db_path)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_migrate_to_sqlite.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add migrate_to_sqlite.py tests/test_migrate_to_sqlite.py
git commit -m "feat: add one-time migrate_to_sqlite.py for cutting over from registry.json"
```

---

### Task 8: Cleanup — retire `registry.py` and the old flat-file layout

**Files:**
- Delete: `registry.py`
- Delete: `tests/test_registry.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing new.
- Produces: nothing new — this task only removes now-dead code and updates docs. `run_weekly.py` needs **no** code changes: `run_all`/`main` already call `fetch.run(config["subreddits"], config["fetch_limit_per_subreddit"], today=today)`, `extract.run(today=today)`, `match.run(today=today)`, `report.run(trend_window_weeks=config["trend_window_weeks"], today=today)` without passing any of the removed path arguments, so they pick up the new `db_path`/`state_path` defaults automatically. `tests/test_run_weekly.py` is unaffected and requires no changes.

- [ ] **Step 1: Confirm nothing still imports `registry.py`**

Run: `grep -rn "import registry\|from registry" --include=*.py .`
Expected: no matches (Tasks 4 and 7 already removed `match.py`'s and `report.py`'s imports; `migrate_to_sqlite.py` was the last consumer and is a one-time script you've now run against your real `data/registry.json`, per Step 2 below)

- [ ] **Step 2: Run the one-time migration against real data, if `data/registry.json` exists**

Run: `python migrate_to_sqlite.py` (uses the default `--registry-path data/registry.json --db-path data/pi_agent.db`)
Expected: exits 0; `data/pi_agent.db` now contains the migrated topics. Skip this step if there is no pre-existing `data/registry.json` to migrate (e.g. a fresh checkout).

- [ ] **Step 3: Delete the old registry module, its test, and the retired data directories**

```bash
git rm registry.py tests/test_registry.py
rm -rf data/raw data/extracted data/registry.json
```

- [ ] **Step 4: Update `README.md`**

Replace the "Architecture" section's stage list and the `data/registry.json` reference with:

```markdown
## Architecture

Four independent pipeline stages chained by a single entrypoint, backed by a SQLite
database (`data/pi_agent.db`) instead of flat JSON files. Each stage is a plain Python
module and can also be run standalone.

```text
run_weekly.py
  └─> fetch.py    → evidence table
  └─> extract.py  → signal_candidate table
  └─> match.py    → canonical_topic + topic_weekly_mentions tables
  └─> report.py   → data/reports/<week>.json, data/reports/<week>.csv
```

- **fetch.py** — pulls new posts per subreddit via PRAW, storing each as an immutable
  `evidence` row.
- **extract.py** — classifies each not-yet-processed evidence row into a `signal_type`
  (from the product intelligence signal taxonomy) plus a confidence score, storing it as
  a `signal_candidate`.
- **match.py** — matches new candidates against the `canonical_topic` registry (or
  creates a new topic), incrementing per-week mention counts.
- **report.py** — computes top topics by mention count and week-over-week trend from the
  DB, unchanged output format.

Use `python query.py topic <slug>` or `python query.py search <keyword>` for ad hoc
inspection of the DB during development.

Credentials (Reddit API + Anthropic API keys) are stored in the OS credential vault via
`keyring` — never in a plaintext file, never committed to the repo.
```

- [ ] **Step 5: Commit**

```bash
git add README.md
git commit -m "chore: retire registry.py and the flat-file data layout"
```

---

### Task 9: End-to-end integration test rewrite

**Files:**
- Modify: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `fetch.run`, `extract.run`, `match.run`, `report.run` with their new signatures (Tasks 2-5), `db.connect`, `db.get_canonical_topic_by_slug`, `db.get_topic_weekly_mentions` (Task 1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_end_to_end.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_end_to_end.py -v`
Expected: FAIL — old version still imports `registry` and calls the pre-migration signatures

- [ ] **Step 3: Fix any integration issues found**

Run the test, and if any stage's real behavior doesn't match what Tasks 2-5 documented (e.g. a field name mismatch), fix the stage module — not the test — unless the test itself has a typo.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_end_to_end.py -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass (`test_config.py`, `test_credentials.py`, `test_db.py`, `test_end_to_end.py`, `test_extract.py`, `test_fetch.py`, `test_match.py`, `test_migrate_to_sqlite.py`, `test_query.py`, `test_report.py`, `test_run_weekly.py`, `test_set_credentials.py`, `test_state.py`, `test_weekutil.py`)

- [ ] **Step 6: Commit**

```bash
git add tests/test_end_to_end.py
git commit -m "test: rewrite end-to-end test against the SQLite-backed pipeline"
```

---

## Self-Review

**Spec coverage:**
- `evidence` / `canonical_topic` / `signal_candidate` / `topic_weekly_mentions` SQLite schema (spec sections 4, 6) — Task 1. ✓
- `fetch.py` writes immutable evidence rows, same field selection and skip/state-timing rules — Task 2. ✓
- `extract.py` classifies into the full section-5 customer-market signal_type taxonomy with a confidence score — Task 3. ✓
- `match.py` matches candidates against canonical topics, creates new topics with slug/name/description, updates weekly mentions — Task 4. ✓
- `report.py` trend computation (new/rising/stable/falling, recency-weighted average) unchanged math, now SQL-sourced — Task 5. ✓
- Ad hoc `query.py` read CLI (explicitly not the formal section-11 API) — Task 6. ✓
- One-time migration from `registry.json`, preserving historical weekly mentions and creating placeholder evidence for existing example permalinks — Task 7. ✓
- Retirement of `registry.py` and the old `data/raw`/`data/extracted`/`data/registry.json` layout — Task 8. ✓
- Per-stage transactional writes (commit-on-success / rollback-on-crash), fail-loud on DB errors — Tasks 2-5, exercised by `test_run_leaves_state_and_db_untouched_when_a_subreddit_fetch_fails` and the `except Exception: conn.rollback()` pattern in every stage's `run()`. ✓
- No new dependency for SQLite (stdlib `sqlite3`) — Task 1, `requirements.txt` untouched. ✓
- End-to-end pipeline test against the new model — Task 9. ✓

**Out of scope, confirmed absent from this plan (deferred to later sub-projects per the spec's Non-goals):** materiality scoring, `product_intelligence.signal.material` event emission, the formal `get_signal`/`get_evidence`/`search_related_signals`/`get_topic_trend`/`search_feedback` API, GitHub Issues/competitor-changelog/manual-interview sources, scheduler/event-driven launch modes, NL query interface, evaluation harness.

**Placeholder scan:** no `TBD`/`TODO`/"implement later" markers; every step includes concrete code or an exact command.

**Type consistency check:** `db.py`'s function names/signatures (Task 1) are used identically in Tasks 2-9 — verified `insert_evidence`, `insert_signal_candidate`, `insert_canonical_topic`, `increment_topic_weekly_mentions`, `get_candidates_without_topic`, `get_evidence_without_candidate`, `get_canonical_topic_by_slug`, `get_topic_weekly_mentions`, `get_evidence_for_topic`, `search_evidence_and_candidates` are called with the same parameter names/order everywhere they appear. `canonical_topic` dict keys (`topic_id`, `name`, `first_seen`, `last_seen`, `slug`, `description`, `aliases`) are used consistently across `match.py`, `report.py`, and `query.py`.
