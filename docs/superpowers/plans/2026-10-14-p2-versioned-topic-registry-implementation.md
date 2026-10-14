# P2: Versioned Topic Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:**
- Keep the history of the canonical topic registry: every definition change, merge and split.
- Make mention counts derived from evidence, so they're reproducible and attributed to the week the evidence was captured.
- Give humans safe merge and split operations.
- Let production weeks become matching-eval scenarios.

**Architecture:**
- `topic_version` stores every topic definition; `canonical_topic` stays the "latest" view, so current readers don't change.
- `topic_lineage` records merges and splits.
- Each candidate keeps `original_topic_id`, the first topic it was matched to. That makes "as of week W" views and undo possible.
- `topic_weekly_mentions` becomes a cache, rebuilt per touched topic from non-duplicate candidates, keyed by the ISO week of `captured_at`.
- A new `registry.py` holds merge, split, the as-of view, history, and a CLI.

**Tech Stack:** Python 3.11, sqlite3, pytest, ruff, PyYAML.

**Spec:** [docs/superpowers/specs/2026-10-14-durability-and-quality-roadmap-design.md](../specs/2026-10-14-durability-and-quality-roadmap-design.md), sections 1.3 (P3, P4), 4, and 5 "P2". **Requires P1 merged.** This plan builds on `db.MIGRATIONS`, `ledger`, `match.resolve_decisions`, `match.apply_resolutions` and `evidence.duplicate_of`.

## Global Constraints

- Everything from P1's Global Constraints still applies: Python 3.11, ruff, no real API calls in tests, UTF-8 file I/O, migrations only, commit trailer.
- This plan's migrations are numbers **6 and 7**, or the next free numbers if plans run out of order (spec §3).
- `canonical_topic` keeps its columns and meaning ("the latest definition"). New columns are additive.
- ISO week strings (`YYYY-Www`, zero-padded) compare correctly as strings. Use plain string comparison for week ordering.
- The registry is changed only by the pipeline (create, match) and by explicit human operations (`registry.py`). No model ever merges or splits topics.

## Review Focus

1. **Running `--stage match` a week late.** Expected: mentions, `first_seen` and `last_seen` land in the week the evidence was captured, not the week match ran. Pinned in Task 2 (`test_late_match_attributes_mentions_to_the_evidence_week`).
2. **The matcher proposing a "new" topic whose slug belongs to a merged topic.** Expected: the candidate goes to the surviving topic; no `UNIQUE` slug error rolls back the stage. Pinned in Task 3 (`test_new_topic_with_a_merged_slug_lands_on_the_survivor`).
3. **A stored match response replayed after a merge** (it names the now-merged topic id). Expected: redirected to the survivor. Pinned in Task 3 (`test_resolve_redirects_merged_topic_ids`).
4. **Asking for the registry as of a week before a merge or split.** Expected: the topics and definitions as they were then: the merged topic present, the split-off topic absent. Pinned in Task 4.
5. **Committing an exported production scenario without reviewing it.** Expected: the golden loader refuses files with `draft: true` and says what to do. Pinned in Task 5.

---

## File Structure

| File | Responsibility |
|---|---|
| `db.py` (modify) | Migrations 6-7; topic version, lineage, status and mention-cache helpers; candidate queries carry `captured_at` |
| `match.py` (modify) | Evidence-week attribution; derived counts; aliases in the prompt; merged-topic redirects |
| `registry.py` (create) | `merge_topics`, `split_topic`, `registry_as_of`, `registry_fingerprint`, `topic_history`, CLI |
| `evals/harness.py` (modify) | Reject `draft: true` matching scenarios |
| `evals/export_matching_scenario.py` (create) | Draft a matching scenario from one production week |
| `docs/decisions/006-versioned-registry-and-derived-mentions.md` (create) | ADR |
| `README.md` (modify) | Registry operations, history, limitations |
| `tests/test_topic_versions.py`, `tests/test_registry.py`, `tests/test_export_matching_scenario.py` (create); `tests/test_match.py`, `tests/test_evals_golden.py` (modify) | Tests |

**Branching:** `git checkout main && git pull && git checkout -b feat/versioned-registry`.

---

### Task 1: Topic versions, status, and original topic

**Files:**
- Modify: `db.py`
- Test: `tests/test_topic_versions.py` (create)

**Interfaces:**
- Consumes: `db.MIGRATIONS`, `db._add_column`, `db.utc_now` (P1).
- Produces:
  - Table `topic_version(topic_id, version, name, description, aliases, valid_from_week, reason, recorded_at, PK(topic_id, version))`
  - Columns `canonical_topic.status TEXT NOT NULL DEFAULT 'active'`, `canonical_topic.merged_into TEXT`, `signal_candidate.original_topic_id TEXT`
  - `db.insert_canonical_topic(conn, *, slug, name, description, aliases, first_seen, last_seen, valid_from_week=None) -> str` (writes version 1)
  - `db.update_topic_definition(conn, topic_id, *, week, reason, name=None, description=None, aliases=None) -> int` (new version number)
  - `db.get_topic_versions(conn, topic_id) -> list[dict]` (ascending; `aliases` decoded)
  - `db.get_topic_version_as_of(conn, topic_id, week) -> dict | None`
  - `db.get_canonical_topics(conn, include_merged=False)`
  - `db.set_candidate_topic(...)` and `db.insert_signal_candidate(...)` set `original_topic_id` the first time a topic is assigned

- [ ] **Step 1: Write the failing tests**

Create `tests/test_topic_versions.py`:

```python
import sqlite3

import pytest

import db


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def _topic(conn, slug="dark-mode", first_seen="2026-W30"):
    return db.insert_canonical_topic(conn, slug=slug, name="Dark mode", description="wants dark theme",
                                     aliases=[], first_seen=first_seen, last_seen=first_seen)


def _candidate(conn, topic_id=None, captured_at="2026-08-15T00:00:00+00:00"):
    evidence_id = db.insert_evidence(conn, source_type="reddit_post", source_name="s", source_url="/x",
                                     captured_at=captured_at, published_at=captured_at, title="t",
                                     content="c", metadata={})
    return db.insert_signal_candidate(conn, evidence_id=evidence_id, signal_type="new_feature_demand",
                                      summary="s", confidence=0.9, topic_id=topic_id)


def test_new_topic_gets_version_one(conn):
    topic_id = _topic(conn)
    [v1] = db.get_topic_versions(conn, topic_id)
    assert (v1["version"], v1["name"], v1["aliases"], v1["valid_from_week"], v1["reason"]) == (
        1, "Dark mode", [], "2026-W30", "created")


def test_update_topic_definition_writes_a_new_version_and_updates_latest(conn):
    topic_id = _topic(conn)

    version = db.update_topic_definition(conn, topic_id, week="2026-W35", reason="clarified",
                                         description="wants a dark UI theme", aliases=["night mode"])

    assert version == 2
    latest = db.get_canonical_topic_by_slug(conn, "dark-mode")
    assert (latest["name"], latest["description"], latest["aliases"]) == ("Dark mode", "wants a dark UI theme", ["night mode"])
    assert [v["description"] for v in db.get_topic_versions(conn, topic_id)] == ["wants dark theme", "wants a dark UI theme"]


def test_version_as_of_picks_the_latest_version_valid_that_week(conn):
    topic_id = _topic(conn)
    db.update_topic_definition(conn, topic_id, week="2026-W35", reason="r", name="Dark theme")

    assert db.get_topic_version_as_of(conn, topic_id, "2026-W29") is None
    assert db.get_topic_version_as_of(conn, topic_id, "2026-W34")["name"] == "Dark mode"
    assert db.get_topic_version_as_of(conn, topic_id, "2026-W35")["name"] == "Dark theme"


def test_get_canonical_topics_hides_merged_topics_by_default(conn):
    keep = _topic(conn, "keep")
    gone = _topic(conn, "gone")
    conn.execute("UPDATE canonical_topic SET status = 'merged', merged_into = ? WHERE topic_id = ?", (keep, gone))

    assert [t["topic_id"] for t in db.get_canonical_topics(conn)] == [keep]
    assert [t["topic_id"] for t in db.get_canonical_topics(conn, include_merged=True)] == [keep, gone]


def test_original_topic_is_set_once(conn):
    first = _topic(conn, "first")
    second = _topic(conn, "second")
    candidate_id = _candidate(conn)

    db.set_candidate_topic(conn, candidate_id, first)
    db.set_candidate_topic(conn, candidate_id, second)

    row = conn.execute("SELECT topic_id, original_topic_id FROM signal_candidate").fetchone()
    assert (row["topic_id"], row["original_topic_id"]) == (second, first)


def test_insert_with_topic_sets_original_topic(conn):
    topic_id = _topic(conn)
    _candidate(conn, topic_id=topic_id)
    assert conn.execute("SELECT original_topic_id FROM signal_candidate").fetchone()[0] == topic_id


def test_migration_backfills_versions_and_original_topics(tmp_path):
    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(str(path))
    raw.executescript(db.SCHEMA_SQL)
    raw.execute("INSERT INTO canonical_topic VALUES ('TOPIC-0001', 'dark-mode', 'Dark mode', 'd', '[]', '2026-W30', '2026-W31')")
    raw.execute("INSERT INTO evidence (evidence_id, source_type, source_name, source_url, captured_at, published_at, "
                "title, content, metadata) VALUES ('EV-2026-000001', 'reddit_post', 's', '/x', "
                "'2026-08-15T00:00:00+00:00', '2026-08-15T00:00:00+00:00', 't', 'c', '{}')")
    raw.execute("INSERT INTO signal_candidate (candidate_id, evidence_id, signal_type, topic_id, summary, confidence) "
                "VALUES ('SC-2026-00001', 'EV-2026-000001', 'new_feature_demand', 'TOPIC-0001', 's', 0.9)")
    raw.commit()
    raw.close()

    conn = db.connect(str(path))
    db.init_db(conn)

    [v1] = db.get_topic_versions(conn, "TOPIC-0001")
    assert (v1["valid_from_week"], v1["reason"]) == ("2026-W30", "backfill")
    assert conn.execute("SELECT original_topic_id FROM signal_candidate").fetchone()[0] == "TOPIC-0001"
    assert db.get_canonical_topic_by_slug(conn, "dark-mode")["status"] == "active"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_topic_versions.py -v`
Expected: FAIL (`no such table: topic_version`, `AttributeError: ... get_topic_versions`).

- [ ] **Step 3: Add migration 6**

```python
def _migration_6_topic_versions(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS topic_version (
          topic_id TEXT NOT NULL REFERENCES canonical_topic(topic_id),
          version INTEGER NOT NULL,
          name TEXT NOT NULL,
          description TEXT,
          aliases TEXT NOT NULL,
          valid_from_week TEXT NOT NULL,
          reason TEXT NOT NULL,
          recorded_at TEXT NOT NULL,
          PRIMARY KEY (topic_id, version)
        )"""
    )
    _add_column(conn, "canonical_topic", "status TEXT NOT NULL DEFAULT 'active'")
    _add_column(conn, "canonical_topic", "merged_into TEXT REFERENCES canonical_topic(topic_id)")
    _add_column(conn, "signal_candidate", "original_topic_id TEXT REFERENCES canonical_topic(topic_id)")
    conn.execute(
        "UPDATE signal_candidate SET original_topic_id = topic_id WHERE topic_id IS NOT NULL AND original_topic_id IS NULL"
    )
    conn.execute(
        "INSERT OR IGNORE INTO topic_version (topic_id, version, name, description, aliases, valid_from_week, reason, "
        "recorded_at) SELECT topic_id, 1, name, description, aliases, first_seen, 'backfill', ? FROM canonical_topic",
        (utc_now(),),
    )
```

Append `(6, _migration_6_topic_versions)` to `MIGRATIONS`.

- [ ] **Step 4: Update the topic and candidate helpers in `db.py`**

Replace `insert_canonical_topic`, `get_canonical_topics` and `set_candidate_topic`. In `insert_signal_candidate`, add `original_topic_id` to the INSERT, set to the same value as `topic_id`. Add the version helpers.

```python
def insert_canonical_topic(conn, *, slug, name, description, aliases, first_seen, last_seen, valid_from_week=None):
    topic_id = _next_sequence_id(conn, "canonical_topic", "topic_id", "TOPIC-", 4)
    try:
        conn.execute(
            "INSERT INTO canonical_topic (topic_id, slug, name, description, aliases, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (topic_id, slug, name, description, json.dumps(aliases), first_seen, last_seen),
        )
    except sqlite3.IntegrityError as e:
        raise DBError(f"cannot create canonical_topic with slug {slug!r}: {e}") from e
    _insert_topic_version(conn, topic_id, 1, name, description, aliases, valid_from_week or first_seen, "created")
    return topic_id


def _insert_topic_version(conn, topic_id, version, name, description, aliases, valid_from_week, reason):
    conn.execute(
        "INSERT INTO topic_version (topic_id, version, name, description, aliases, valid_from_week, reason, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (topic_id, version, name, description, json.dumps(aliases), valid_from_week, reason, utc_now()),
    )


def update_topic_definition(conn, topic_id, *, week, reason, name=None, description=None, aliases=None):
    current = conn.execute("SELECT * FROM canonical_topic WHERE topic_id = ?", (topic_id,)).fetchone()
    if current is None:
        raise DBError(f"no topic {topic_id}")
    new_name = name if name is not None else current["name"]
    new_description = description if description is not None else current["description"]
    new_aliases = aliases if aliases is not None else json.loads(current["aliases"])
    conn.execute(
        "UPDATE canonical_topic SET name = ?, description = ?, aliases = ? WHERE topic_id = ?",
        (new_name, new_description, json.dumps(new_aliases), topic_id),
    )
    version = conn.execute("SELECT MAX(version) FROM topic_version WHERE topic_id = ?", (topic_id,)).fetchone()[0] + 1
    _insert_topic_version(conn, topic_id, version, new_name, new_description, new_aliases, week, reason)
    return version


def _topic_version_row_to_dict(row):
    d = dict(row)
    d["aliases"] = json.loads(d["aliases"])
    return d


def get_topic_versions(conn, topic_id):
    rows = conn.execute("SELECT * FROM topic_version WHERE topic_id = ? ORDER BY version", (topic_id,)).fetchall()
    return [_topic_version_row_to_dict(r) for r in rows]


def get_topic_version_as_of(conn, topic_id, week):
    row = conn.execute(
        "SELECT * FROM topic_version WHERE topic_id = ? AND valid_from_week <= ? ORDER BY version DESC LIMIT 1",
        (topic_id, week),
    ).fetchone()
    return _topic_version_row_to_dict(row) if row else None


def get_canonical_topics(conn, include_merged=False):
    where = "" if include_merged else " WHERE status = 'active'"
    rows = conn.execute(f"SELECT * FROM canonical_topic{where} ORDER BY topic_id").fetchall()
    return [_topic_row_to_dict(r) for r in rows]


def set_candidate_topic(conn, candidate_id, topic_id, match_call_id=None):
    conn.execute(
        "UPDATE signal_candidate SET topic_id = ?, original_topic_id = COALESCE(original_topic_id, ?), "
        "match_call_id = COALESCE(?, match_call_id), match_error = NULL WHERE candidate_id = ?",
        (topic_id, topic_id, match_call_id, candidate_id),
    )
```

The new `insert_signal_candidate` INSERT column list ends with `..., llm_call_id, original_topic_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`, with `topic_id` passed again as the last value.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_topic_versions.py tests/test_db.py tests/test_match.py -v`
Expected: PASS.

- [ ] **Step 6: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add db.py tests/test_topic_versions.py
git commit -m "feat(db): version every topic definition and remember each candidate's first topic"
```

---

### Task 2: Derived mention counts, attributed to the evidence week

**Files:**
- Modify: `db.py` (mention cache, `extend_topic_seen`, candidate queries carry `captured_at`, duplicates excluded)
- Modify: `match.py` (`candidate_week`, `apply_resolutions`)
- Test: `tests/test_match.py` (append), `tests/test_topic_versions.py` (append)

**Interfaces:**
- Consumes: Task 1; P1's `match.apply_resolutions`.
- Produces:
  - `db.derived_topic_weekly_mentions(conn, topic_id) -> dict[str, int]`
  - `db.refresh_topic_weekly_mentions(conn, topic_ids)`
  - `db.rebuild_all_topic_weekly_mentions(conn) -> dict[str, tuple[dict, dict]]` (topic id → (before, after), changed topics only)
  - `db.extend_topic_seen(conn, topic_id, week)` (`first_seen = MIN`, `last_seen = MAX`)
  - `db.resolve_topic_id(conn, topic_id) -> str` (follows `merged_into` to an active topic; used in Task 3, defined here)
  - `db.get_candidates_without_topic` and `db.get_candidates_by_ids` rows include `captured_at`
  - `db.get_candidates_for_topic` excludes duplicate evidence
  - `match.candidate_week(candidate, fallback) -> str`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_match.py`:

```python
def test_late_match_attributes_mentions_to_the_evidence_week(tmp_path):
    conn = seed_db(tmp_path)
    seed_candidate(conn, "User wants CSV export")  # captured 2026-08-15 = 2026-W33
    conn.close()
    response = json.dumps([{"index": 0, "matched_topic_id": None,
                            "new_topic": {"name": "Export to CSV", "slug": "export-to-csv", "description": "d"}}])

    match.run(db_path=str(tmp_path / "pi_agent.db"), today=date(2026, 8, 29), client=FakeClient([response]))  # W35

    conn = db.connect(str(tmp_path / "pi_agent.db"))
    topic = db.get_canonical_topic_by_slug(conn, "export-to-csv")
    assert (topic["first_seen"], topic["last_seen"]) == ("2026-W33", "2026-W33")
    assert db.get_topic_weekly_mentions(conn, topic["topic_id"]) == {"2026-W33": 1}


def test_matching_older_evidence_widens_first_seen_but_never_narrows_last_seen(tmp_path):
    conn = seed_db(tmp_path)
    topic_id = seed_existing_topic(conn)  # first_seen W30, last_seen W31
    evidence_id = db.insert_evidence(conn, source_type="reddit_post", source_name="s", source_url="/old",
                                     captured_at="2026-07-06T00:00:00+00:00", published_at="2026-07-06T00:00:00+00:00",
                                     title="t", content="c", metadata={})  # 2026-W28
    db.insert_signal_candidate(conn, evidence_id=evidence_id, signal_type="new_feature_demand", summary="s", confidence=0.9)
    conn.commit()
    candidates = db.get_candidates_without_topic(conn)

    match.apply_matches(conn, candidates, [{"index": 0, "matched_topic_id": topic_id}], week="2026-W35")

    topic = db.get_canonical_topic_by_slug(conn, "dark-mode-support")
    assert (topic["first_seen"], topic["last_seen"]) == ("2026-W28", "2026-W31")
```

Append to `tests/test_topic_versions.py`:

```python
def test_derived_mentions_count_non_duplicate_candidates_by_captured_week(conn):
    topic_id = _topic(conn)
    _candidate(conn, topic_id, "2026-08-10T00:00:00+00:00")  # W33
    _candidate(conn, topic_id, "2026-08-15T00:00:00+00:00")  # W33
    dup = _candidate(conn, topic_id, "2026-08-20T00:00:00+00:00")  # W34, then marked duplicate
    evidence_id = conn.execute("SELECT evidence_id FROM signal_candidate WHERE candidate_id = ?", (dup,)).fetchone()[0]
    conn.execute("UPDATE evidence SET duplicate_of = 'EV-2026-000001' WHERE evidence_id = ?", (evidence_id,))

    assert db.derived_topic_weekly_mentions(conn, topic_id) == {"2026-W33": 2}


def test_refresh_replaces_cached_counts(conn):
    topic_id = _topic(conn)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=5)
    _candidate(conn, topic_id, "2026-08-15T00:00:00+00:00")

    db.refresh_topic_weekly_mentions(conn, [topic_id])

    assert db.get_topic_weekly_mentions(conn, topic_id) == {"2026-W33": 1}


def test_rebuild_all_reports_only_changed_topics(conn):
    changed = _topic(conn, "changed")
    same = _topic(conn, "same")
    db.increment_topic_weekly_mentions(conn, changed, "2026-W30", amount=2)
    _candidate(conn, changed, "2026-08-15T00:00:00+00:00")
    _candidate(conn, same, "2026-08-15T00:00:00+00:00")
    db.refresh_topic_weekly_mentions(conn, [same])

    diffs = db.rebuild_all_topic_weekly_mentions(conn)

    assert diffs == {changed: ({"2026-W30": 2}, {"2026-W33": 1})}
    assert db.get_topic_weekly_mentions(conn, changed) == {"2026-W33": 1}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_match.py tests/test_topic_versions.py -v`
Expected: the new tests FAIL (mentions keyed `2026-W35`; missing `derived_topic_weekly_mentions`).

- [ ] **Step 3: Add the helpers to `db.py`**

Add `from collections import Counter` and `from datetime import date` to the imports, alongside the existing `datetime, timezone` import (`from datetime import date, datetime, timezone`).

```python
def _week_of_timestamp(iso_timestamp):
    return iso_week_string(date.fromisoformat(iso_timestamp[:10]))


def derived_topic_weekly_mentions(conn, topic_id):
    rows = conn.execute(
        "SELECT e.captured_at FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id "
        "WHERE sc.topic_id = ? AND e.duplicate_of IS NULL",
        (topic_id,),
    ).fetchall()
    return dict(Counter(_week_of_timestamp(r["captured_at"]) for r in rows))


def refresh_topic_weekly_mentions(conn, topic_ids):
    """Rebuild the cached weekly counts of these topics from their non-duplicate candidates."""
    for topic_id in sorted(set(topic_ids)):
        conn.execute("DELETE FROM topic_weekly_mentions WHERE topic_id = ?", (topic_id,))
        for period, mentions in derived_topic_weekly_mentions(conn, topic_id).items():
            conn.execute(
                "INSERT INTO topic_weekly_mentions (topic_id, period, mentions) VALUES (?, ?, ?)",
                (topic_id, period, mentions),
            )


def rebuild_all_topic_weekly_mentions(conn):
    """Refresh every topic's cache. Returns {topic_id: (before, after)} for topics whose counts changed."""
    diffs = {}
    for topic in get_canonical_topics(conn, include_merged=True):
        before = get_topic_weekly_mentions(conn, topic["topic_id"])
        after = derived_topic_weekly_mentions(conn, topic["topic_id"])
        if before != after:
            diffs[topic["topic_id"]] = (before, after)
            refresh_topic_weekly_mentions(conn, [topic["topic_id"]])
    return diffs


def extend_topic_seen(conn, topic_id, week):
    conn.execute(
        "UPDATE canonical_topic SET first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?) WHERE topic_id = ?",
        (week, week, topic_id),
    )


def resolve_topic_id(conn, topic_id):
    """Follow merged_into links to the active topic a (possibly merged) topic now lives in."""
    seen = set()
    while topic_id not in seen:
        seen.add(topic_id)
        row = conn.execute("SELECT status, merged_into FROM canonical_topic WHERE topic_id = ?", (topic_id,)).fetchone()
        if row is None or row["status"] != "merged" or not row["merged_into"]:
            return topic_id
        topic_id = row["merged_into"]
    raise DBError(f"merge cycle involving {topic_id}")
```

Change the two candidate readers to join evidence and carry `captured_at`, and make `get_candidates_for_topic` skip duplicates:

```python
_CANDIDATE_WITH_WEEK = "SELECT sc.*, e.captured_at FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id"


def get_candidates_without_topic(conn, max_attempts=None):
    sql, params = _CANDIDATE_WITH_WEEK + " WHERE sc.topic_id IS NULL", ()
    if max_attempts is not None:
        sql, params = sql + " AND sc.match_attempts < ?", (max_attempts,)
    rows = conn.execute(sql + " ORDER BY sc.candidate_id", params).fetchall()
    return [_signal_candidate_row_to_dict(r) for r in rows]


def get_candidates_by_ids(conn, candidate_ids):
    if not candidate_ids:
        return []
    rows = {r["candidate_id"]: r for r in conn.execute(
        _CANDIDATE_WITH_WEEK + f" WHERE sc.candidate_id IN ({','.join('?' * len(candidate_ids))})", tuple(candidate_ids),
    )}
    return [_signal_candidate_row_to_dict(rows[i]) for i in candidate_ids if i in rows]
```

In `get_candidates_for_topic`, change `"WHERE sc.topic_id = ? ORDER BY sc.candidate_id"` to `"WHERE sc.topic_id = ? AND e.duplicate_of IS NULL ORDER BY sc.candidate_id"`.

- [ ] **Step 4: Update `match.apply_resolutions`**

Add `from datetime import date` (already imported) and use `iso_week_string` (already imported). Add `candidate_week`, and replace `apply_resolutions`:

```python
def candidate_week(candidate, fallback):
    """ISO week the candidate's evidence was captured in; mentions and first/last seen use it."""
    captured_at = candidate.get("captured_at")
    return iso_week_string(date.fromisoformat(captured_at[:10])) if captured_at else fallback


def apply_resolutions(conn, candidates, resolutions, week, match_call_id=None):
    """Write resolutions to the database. Does not commit. Invalid ones only count an attempt.
    `week` is used only for candidates that carry no captured_at."""
    outcome = ApplyOutcome()
    created_by_slug = {}
    touched = set()
    for candidate, resolution in zip(candidates, resolutions):
        candidate_id = candidate["candidate_id"]
        if resolution.action == "invalid":
            db.record_match_failure(conn, candidate_id, resolution.reason)
            outcome.invalid.append((candidate_id, resolution.reason))
            continue

        seen_week = candidate_week(candidate, week)
        if resolution.action == "match":
            topic_id = resolution.topic_id
            outcome.matched += 1
        else:
            slug = resolution.new_topic["slug"]
            topic_id = created_by_slug.get(slug)
            if topic_id is not None:
                outcome.matched += 1
            else:
                existing = db.get_canonical_topic_by_slug(conn, slug)
                if existing is not None:
                    topic_id = db.resolve_topic_id(conn, existing["topic_id"])
                    outcome.matched += 1
                else:
                    topic_id = db.insert_canonical_topic(
                        conn, slug=slug, name=resolution.new_topic["name"],
                        description=resolution.new_topic["description"], aliases=[],
                        first_seen=seen_week, last_seen=seen_week,
                    )
                    outcome.created.append(topic_id)
                created_by_slug[slug] = topic_id

        db.extend_topic_seen(conn, topic_id, seen_week)
        db.set_candidate_topic(conn, candidate_id, topic_id, match_call_id=match_call_id)
        touched.add(topic_id)

    db.refresh_topic_weekly_mentions(conn, touched)
    return outcome
```

`update_topic_last_seen` and `increment_topic_weekly_mentions` are no longer called by `match.py`. Keep both in `db.py`: tests use `increment_topic_weekly_mentions` to seed history, and nothing else needs deleting in this task.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_match.py tests/test_topic_versions.py tests/test_end_to_end.py tests/test_resume.py tests/test_materiality.py tests/test_query.py -v`
Expected: PASS. `test_apply_matches_increments_existing_topic_on_match` still passes: it asserts only the `2026-W33` count, which the refresh derives from the one candidate.

- [ ] **Step 6: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add db.py match.py tests/test_match.py tests/test_topic_versions.py
git commit -m "feat(match): derive weekly mentions from evidence and attribute them to the capture week"
```

---

### Task 3: Merge and split, with lineage; matcher sees aliases and follows merges

**Files:**
- Modify: `db.py` (migration 7; lineage, status and move helpers)
- Create: `registry.py`
- Modify: `match.py` (`build_matching_prompt` aliases; `resolve_decisions(..., redirects=None)`; `match_batch`, `replay_unapplied`, `apply_matches` pass redirects)
- Test: `tests/test_registry.py` (create), `tests/test_match_resolution.py` (append), `tests/test_match.py` (append)

**Interfaces:**
- Consumes: Tasks 1-2.
- Produces:
  - Table `topic_lineage(lineage_id INTEGER PK AUTOINCREMENT, kind, from_topic_id, to_topic_id, week, reason, decided_by, candidate_ids, created_at)`
  - `db.insert_topic_lineage(conn, *, kind, from_topic_id, to_topic_id, week, reason, decided_by, candidate_ids) -> int`
  - `db.get_lineage_for_topic(conn, topic_id) -> list[dict]`
  - `db.set_topic_status(conn, topic_id, status, merged_into=None)`
  - `db.move_candidates(conn, from_topic_id, to_topic_id, candidate_ids=None) -> list[str]`
  - `db.get_merge_redirects(conn) -> dict[str, str]` (merged id → active id)
  - `registry.RegistryError(Exception)`
  - `registry.merge_topics(conn, from_slug, into_slug, *, week, reason, decided_by) -> dict` (`{"from", "into", "moved"}`; no commit)
  - `registry.split_topic(conn, slug, *, new_name, new_slug, new_description, candidate_ids, week, reason, decided_by) -> dict` (`{"from", "new", "moved"}`; no commit)
  - `match.resolve_decisions(candidates, decisions, existing_topics, redirects=None)`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_registry.py`:

```python
import pytest

import db
import registry


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    db.init_db(c)
    yield c
    c.close()


def topic(conn, slug, name, first_seen="2026-W30"):
    return db.insert_canonical_topic(conn, slug=slug, name=name, description=f"{name} description",
                                     aliases=[], first_seen=first_seen, last_seen=first_seen)


def candidate(conn, topic_id, captured_at="2026-08-15T00:00:00+00:00", n=[0]):
    n[0] += 1
    evidence_id = db.insert_evidence(conn, source_type="reddit_post", source_name="s", source_url=f"/{n[0]}",
                                     captured_at=captured_at, published_at=captured_at, title="t", content="c", metadata={})
    candidate_id = db.insert_signal_candidate(conn, evidence_id=evidence_id, signal_type="new_feature_demand",
                                              summary="s", confidence=0.9, topic_id=topic_id)
    db.refresh_topic_weekly_mentions(conn, [topic_id])
    return candidate_id


def test_merge_moves_candidates_retires_the_source_and_records_lineage(conn):
    keep = topic(conn, "dark-mode", "Dark mode")
    gone = topic(conn, "night-theme", "Night theme", first_seen="2026-W28")
    moved = candidate(conn, gone, "2026-07-08T00:00:00+00:00")  # W28
    candidate(conn, keep)

    result = registry.merge_topics(conn, "night-theme", "dark-mode", week="2026-W40",
                                   reason="same request", decided_by="human:test")

    assert result == {"from": gone, "into": keep, "moved": [moved]}
    source = db.get_canonical_topic_by_slug(conn, "night-theme")
    assert (source["status"], source["merged_into"]) == ("merged", keep)
    survivor = db.get_canonical_topic_by_slug(conn, "dark-mode")
    assert survivor["aliases"] == ["Night theme"]
    assert survivor["first_seen"] == "2026-W28"
    assert db.get_topic_weekly_mentions(conn, keep) == {"2026-W28": 1, "2026-W33": 1}
    assert db.get_topic_weekly_mentions(conn, gone) == {}
    [lineage] = db.get_lineage_for_topic(conn, gone)
    assert (lineage["kind"], lineage["to_topic_id"], lineage["candidate_ids"]) == ("merge", keep, [moved])
    row = conn.execute("SELECT topic_id, original_topic_id FROM signal_candidate WHERE candidate_id = ?", (moved,)).fetchone()
    assert (row["topic_id"], row["original_topic_id"]) == (keep, gone)


@pytest.mark.parametrize("from_slug,into_slug,message", [
    ("missing", "dark-mode", "no topic"),
    ("dark-mode", "dark-mode", "itself"),
])
def test_merge_rejects_bad_requests(conn, from_slug, into_slug, message):
    topic(conn, "dark-mode", "Dark mode")
    with pytest.raises(registry.RegistryError, match=message):
        registry.merge_topics(conn, from_slug, into_slug, week="2026-W40", reason="r", decided_by="h")


def test_merge_rejects_an_already_merged_topic(conn):
    keep = topic(conn, "a", "A")
    topic(conn, "b", "B")
    topic(conn, "c", "C")
    registry.merge_topics(conn, "b", "a", week="2026-W40", reason="r", decided_by="h")
    with pytest.raises(registry.RegistryError, match="already merged"):
        registry.merge_topics(conn, "b", "c", week="2026-W40", reason="r", decided_by="h")
    assert db.get_merge_redirects(conn) == {db.get_canonical_topic_by_slug(conn, "b")["topic_id"]: keep}


def test_split_moves_the_named_candidates_into_a_new_topic(conn):
    source = topic(conn, "exports", "Exports")
    csv_one = candidate(conn, source, "2026-08-10T00:00:00+00:00")
    candidate(conn, source)
    csv_two = candidate(conn, source, "2026-08-20T00:00:00+00:00")

    result = registry.split_topic(conn, "exports", new_name="Export to CSV", new_slug="Export to CSV",
                                  new_description="CSV specifically", candidate_ids=[csv_one, csv_two],
                                  week="2026-W40", reason="two issues", decided_by="human:test")

    new = db.get_canonical_topic_by_slug(conn, "export-to-csv")
    assert result == {"from": source, "new": new["topic_id"], "moved": [csv_one, csv_two]}
    assert (new["first_seen"], new["last_seen"]) == ("2026-W33", "2026-W34")
    assert db.get_topic_versions(conn, new["topic_id"])[0]["valid_from_week"] == "2026-W40"
    assert db.get_topic_weekly_mentions(conn, source) == {"2026-W33": 1}


def test_split_rejects_candidates_from_another_topic_or_moving_everything(conn):
    source = topic(conn, "exports", "Exports")
    other = topic(conn, "other", "Other")
    only = candidate(conn, source)
    foreign = candidate(conn, other)
    with pytest.raises(registry.RegistryError, match="not in topic"):
        registry.split_topic(conn, "exports", new_name="X", new_slug="x", new_description="d",
                             candidate_ids=[foreign], week="2026-W40", reason="r", decided_by="h")
    with pytest.raises(registry.RegistryError, match="every candidate"):
        registry.split_topic(conn, "exports", new_name="X", new_slug="x", new_description="d",
                             candidate_ids=[only], week="2026-W40", reason="r", decided_by="h")
```

Append to `tests/test_match_resolution.py`:

```python
def test_resolve_redirects_merged_topic_ids():
    [r] = match.resolve_decisions(CANDS[:1], [{"index": 0, "matched_topic_id": "TOPIC-0007"}], TOPICS,
                                  redirects={"TOPIC-0007": "TOPIC-0001"})
    assert (r.action, r.topic_id) == ("match", "TOPIC-0001")
    assert "merged" in r.reason


def test_matching_prompt_lists_aliases_only_when_present():
    with_alias = [{"topic_id": "TOPIC-0001", "name": "Dark mode", "description": "d", "aliases": ["Night theme"]}]
    without = [{"topic_id": "TOPIC-0001", "name": "Dark mode", "description": "d", "aliases": []}]
    cands = [{"signal_type": "new_feature_demand", "summary": "s"}]
    assert "Night theme" in match.build_matching_prompt(cands, with_alias)
    assert '"aliases"' not in match.build_matching_prompt(cands, without)
```

Append to `tests/test_match.py`:

```python
import registry


def test_new_topic_with_a_merged_slug_lands_on_the_survivor(tmp_path):
    conn = seed_db(tmp_path)
    keep = seed_existing_topic(conn)  # dark-mode-support
    db.insert_canonical_topic(conn, slug="night-theme", name="Night theme", description="d",
                              aliases=[], first_seen="2026-W30", last_seen="2026-W30")
    registry.merge_topics(conn, "night-theme", "dark-mode-support", week="2026-W32", reason="r", decided_by="h")
    seed_candidate(conn, "night theme please")
    candidates = db.get_candidates_without_topic(conn)
    decision = {"index": 0, "matched_topic_id": None,
                "new_topic": {"name": "Night theme", "slug": "night-theme", "description": "d"}}

    match.apply_matches(conn, candidates, [decision], week="2026-W33")

    assert conn.execute("SELECT topic_id FROM signal_candidate").fetchone()[0] == keep
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_registry.py tests/test_match_resolution.py tests/test_match.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'registry'`, `unexpected keyword argument 'redirects'`).

- [ ] **Step 3: Add migration 7 and the helpers to `db.py`**

```python
def _migration_7_topic_lineage(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS topic_lineage (
          lineage_id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          from_topic_id TEXT NOT NULL REFERENCES canonical_topic(topic_id),
          to_topic_id TEXT NOT NULL REFERENCES canonical_topic(topic_id),
          week TEXT NOT NULL,
          reason TEXT NOT NULL,
          decided_by TEXT NOT NULL,
          candidate_ids TEXT NOT NULL,
          created_at TEXT NOT NULL
        )"""
    )


def insert_topic_lineage(conn, *, kind, from_topic_id, to_topic_id, week, reason, decided_by, candidate_ids):
    cursor = conn.execute(
        "INSERT INTO topic_lineage (kind, from_topic_id, to_topic_id, week, reason, decided_by, candidate_ids, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, from_topic_id, to_topic_id, week, reason, decided_by, json.dumps(candidate_ids), utc_now()),
    )
    return cursor.lastrowid


def get_lineage_for_topic(conn, topic_id):
    rows = conn.execute(
        "SELECT * FROM topic_lineage WHERE from_topic_id = ? OR to_topic_id = ? ORDER BY lineage_id", (topic_id, topic_id)
    ).fetchall()
    return [{**dict(r), "candidate_ids": json.loads(r["candidate_ids"])} for r in rows]


def set_topic_status(conn, topic_id, status, merged_into=None):
    conn.execute("UPDATE canonical_topic SET status = ?, merged_into = ? WHERE topic_id = ?", (status, merged_into, topic_id))


def move_candidates(conn, from_topic_id, to_topic_id, candidate_ids=None):
    """Re-point candidates (all of from_topic_id's, or just candidate_ids). original_topic_id is kept."""
    if candidate_ids is None:
        candidate_ids = [r[0] for r in conn.execute(
            "SELECT candidate_id FROM signal_candidate WHERE topic_id = ? ORDER BY candidate_id", (from_topic_id,))]
    for candidate_id in candidate_ids:
        conn.execute("UPDATE signal_candidate SET topic_id = ? WHERE candidate_id = ? AND topic_id = ?",
                     (to_topic_id, candidate_id, from_topic_id))
    return list(candidate_ids)


def get_merge_redirects(conn):
    rows = conn.execute("SELECT topic_id FROM canonical_topic WHERE status = 'merged'").fetchall()
    return {r["topic_id"]: resolve_topic_id(conn, r["topic_id"]) for r in rows}
```

Append `(7, _migration_7_topic_lineage)` to `MIGRATIONS`.

- [ ] **Step 4: Create `registry.py`**

```python
"""Human operations on the canonical topic registry: merge, split, history and as-of views.

The pipeline only creates topics and matches candidates to them. Merges and splits happen here,
on purpose, by a person (or a person accepting a review item, P5). Every operation keeps history:
topic_version for definitions, topic_lineage for merges and splits, original_topic_id on candidates.
"""
import db
import match


class RegistryError(Exception):
    pass


def _active_topic(conn, slug):
    topic = db.get_canonical_topic_by_slug(conn, slug)
    if topic is None:
        raise RegistryError(f"no topic with slug {slug!r}")
    if topic["status"] != "active":
        raise RegistryError(f"topic {slug!r} is already merged into {topic['merged_into']}")
    return topic


def merge_topics(conn, from_slug, into_slug, *, week, reason, decided_by):
    """Retire from_slug into into_slug. Does not commit."""
    if from_slug == into_slug:
        raise RegistryError("cannot merge a topic into itself")
    source, target = _active_topic(conn, from_slug), _active_topic(conn, into_slug)

    moved = db.move_candidates(conn, source["topic_id"], target["topic_id"])
    db.set_topic_status(conn, source["topic_id"], "merged", merged_into=target["topic_id"])

    aliases = list(target["aliases"])
    for alias in [source["name"], *source["aliases"]]:
        if alias != target["name"] and alias not in aliases:
            aliases.append(alias)
    db.update_topic_definition(conn, target["topic_id"], week=week, reason=f"merged {source['topic_id']}", aliases=aliases)
    db.extend_topic_seen(conn, target["topic_id"], source["first_seen"])
    db.extend_topic_seen(conn, target["topic_id"], source["last_seen"])

    db.insert_topic_lineage(conn, kind="merge", from_topic_id=source["topic_id"], to_topic_id=target["topic_id"],
                            week=week, reason=reason, decided_by=decided_by, candidate_ids=moved)
    db.refresh_topic_weekly_mentions(conn, [source["topic_id"], target["topic_id"]])
    return {"from": source["topic_id"], "into": target["topic_id"], "moved": moved}


def split_topic(conn, slug, *, new_name, new_slug, new_description, candidate_ids, week, reason, decided_by):
    """Move candidate_ids out of `slug` into a new topic. Does not commit."""
    source = _active_topic(conn, slug)
    slug_new = match.normalize_slug(new_slug) or match.normalize_slug(new_name)
    if not slug_new:
        raise RegistryError("the new topic needs a usable slug")
    if db.get_canonical_topic_by_slug(conn, slug_new) is not None:
        raise RegistryError(f"slug {slug_new!r} already exists")
    if not candidate_ids:
        raise RegistryError("name at least one candidate to move")

    candidates = db.get_candidates_by_ids(conn, list(candidate_ids))
    found = {c["candidate_id"] for c in candidates if c["topic_id"] == source["topic_id"]}
    missing = [c for c in candidate_ids if c not in found]
    if missing:
        raise RegistryError(f"candidates not in topic {slug!r}: {missing}")
    total = conn.execute("SELECT COUNT(*) FROM signal_candidate WHERE topic_id = ?", (source["topic_id"],)).fetchone()[0]
    if len(found) == total:
        raise RegistryError("moving every candidate is a rename, not a split; edit the topic definition instead")

    weeks = sorted(match.candidate_week(c, week) for c in candidates)
    new_id = db.insert_canonical_topic(conn, slug=slug_new, name=new_name, description=new_description, aliases=[],
                                       first_seen=weeks[0], last_seen=weeks[-1], valid_from_week=week)
    moved = db.move_candidates(conn, source["topic_id"], new_id, list(candidate_ids))
    db.insert_topic_lineage(conn, kind="split", from_topic_id=source["topic_id"], to_topic_id=new_id,
                            week=week, reason=reason, decided_by=decided_by, candidate_ids=moved)
    db.refresh_topic_weekly_mentions(conn, [source["topic_id"], new_id])
    return {"from": source["topic_id"], "new": new_id, "moved": moved}
```

- [ ] **Step 5: Teach `match.py` about aliases and redirects**

In `build_matching_prompt`, replace the `existing_summary` comprehension with:

```python
    existing_summary = [
        {"topic_id": t["topic_id"], "name": t["name"], "description": t["description"],
         **({"aliases": t["aliases"]} if t.get("aliases") else {})}
        for t in existing_topics
    ]
```

Change `resolve_decisions` and `_resolve_one`:

```python
def resolve_decisions(candidates, decisions, existing_topics, redirects=None):
    """... (docstring unchanged) `redirects` maps merged topic ids to the active topic they live in now."""
    redirects = redirects or {}
    known_ids = {t["topic_id"] for t in existing_topics}
    id_by_slug = {t["slug"]: t["topic_id"] for t in existing_topics if t.get("slug")}
    # ... unchanged until the per-index loop, which now calls:
    #     _resolve_one(i, got[0], known_ids, id_by_slug, redirects)


def _resolve_one(index, decision, known_ids, id_by_slug, redirects):
    topic_id = decision.get("matched_topic_id")
    new_topic = decision.get("new_topic")
    if topic_id:
        if new_topic:
            return Resolution(index, "invalid", reason="both matched_topic_id and new_topic are set")
        if isinstance(topic_id, str) and redirects.get(topic_id) in known_ids:
            return Resolution(index, "match", topic_id=redirects[topic_id],
                              reason=f"redirected from merged topic {topic_id}")
        if not isinstance(topic_id, str) or topic_id not in known_ids:
            return Resolution(index, "invalid", reason=f"unknown topic id {topic_id!r}")
        return Resolution(index, "match", topic_id=topic_id)
    # ... new_topic branch unchanged
```

Pass redirects at the three call sites:

```python
# in apply_matches:
    return apply_resolutions(conn, candidates,
                             resolve_decisions(candidates, decisions, existing, db.get_merge_redirects(conn)), week)

# in _apply_call:
    resolutions = resolve_decisions(candidates, decisions, existing, db.get_merge_redirects(conn))
```

`replay_unapplied` and `match_batch` reach `resolve_decisions` through `_apply_call`, so they pick up redirects without further changes.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_registry.py tests/test_match_resolution.py tests/test_match.py tests/test_evals_harness.py tests/test_evals_runners.py -v`
Expected: PASS. Golden topics carry no aliases, so eval prompts are byte-for-byte unchanged.

- [ ] **Step 7: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add db.py registry.py match.py tests/test_registry.py tests/test_match_resolution.py tests/test_match.py
git commit -m "feat(registry): merge and split topics with lineage; matcher sees aliases and follows merges"
```

---

### Task 4: As-of view, history, CLI, and registry fingerprints on match calls

**Files:**
- Modify: `registry.py` (as-of view, fingerprint, history, CLI)
- Modify: `match.py` (`match_batch` adds `registry_fingerprint` to `request_meta`)
- Test: `tests/test_registry.py` (append), `tests/test_match.py` (append)

**Interfaces:**
- Consumes: Tasks 1-3; `fingerprints.fingerprint` (P1).
- Produces:
  - `registry.registry_as_of(conn, week) -> list[dict]` (keys `topic_id`, `slug`, `name`, `description`, `aliases`; sorted by `topic_id`)
  - `registry.registry_fingerprint(topics) -> str`
  - `registry.topic_history(conn, slug) -> dict` (keys `topic`, `versions`, `lineage`, `weekly_mentions`)
  - `registry.main(argv=None) -> int`, with subcommands `history SLUG`, `merge FROM INTO --reason R [--by NAME]`, `split SLUG --new-name N --new-slug S --description D --candidates ID[,ID...] --reason R [--by NAME]`, `snapshot --week W`, `rebuild-mentions [--apply]`. All take `--db-path`.
  - Match `request_meta` gains `"registry_fingerprint"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_registry.py`:

```python
import json

from fingerprints import fingerprint


def test_registry_as_of_shows_topics_and_definitions_as_they_were(conn):
    keep = topic(conn, "dark-mode", "Dark mode", first_seen="2026-W30")
    topic(conn, "night-theme", "Night theme", first_seen="2026-W31")
    source = topic(conn, "exports", "Exports", first_seen="2026-W30")
    first = candidate(conn, source)
    candidate(conn, source)
    registry.merge_topics(conn, "night-theme", "dark-mode", week="2026-W36", reason="r", decided_by="h")
    registry.split_topic(conn, "exports", new_name="CSV", new_slug="csv", new_description="d",
                         candidate_ids=[first], week="2026-W37", reason="r", decided_by="h")

    before = registry.registry_as_of(conn, "2026-W35")
    after = registry.registry_as_of(conn, "2026-W37")

    assert [t["slug"] for t in before] == ["dark-mode", "night-theme", "exports"]
    assert next(t for t in before if t["topic_id"] == keep)["aliases"] == []
    assert [t["slug"] for t in after] == ["dark-mode", "exports", "csv"]
    assert next(t for t in after if t["topic_id"] == keep)["aliases"] == ["Night theme"]


def test_registry_fingerprint_ignores_order_and_tracks_definitions():
    a = [{"topic_id": "T1", "name": "A", "description": "d", "aliases": []},
         {"topic_id": "T2", "name": "B", "description": "d", "aliases": []}]
    assert registry.registry_fingerprint(a) == registry.registry_fingerprint(list(reversed(a)))
    changed = [dict(a[0], description="other"), a[1]]
    assert registry.registry_fingerprint(a) != registry.registry_fingerprint(changed)
    assert registry.registry_fingerprint([]) == fingerprint([])


def test_topic_history_collects_versions_lineage_and_mentions(conn):
    topic(conn, "dark-mode", "Dark mode")
    topic(conn, "night-theme", "Night theme")
    registry.merge_topics(conn, "night-theme", "dark-mode", week="2026-W36", reason="r", decided_by="h")

    history = registry.topic_history(conn, "dark-mode")

    assert [v["version"] for v in history["versions"]] == [1, 2]
    assert history["lineage"][0]["kind"] == "merge"
    assert history["weekly_mentions"] == {}


def test_cli_merge_commits_and_history_prints_json(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    c = db.connect(path)
    db.init_db(c)
    topic(c, "dark-mode", "Dark mode")
    topic(c, "night-theme", "Night theme")
    c.commit()
    c.close()

    assert registry.main(["--db-path", path, "merge", "night-theme", "dark-mode", "--reason", "same"]) == 0
    capsys.readouterr()
    assert registry.main(["--db-path", path, "history", "dark-mode"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["topic"]["aliases"] == ["Night theme"]
    assert output["lineage"][0]["decided_by"] == "human:cli"


def test_cli_reports_registry_errors_with_exit_code_2(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    assert registry.main(["--db-path", path, "merge", "a", "b", "--reason", "r"]) == 2
    assert "no topic" in capsys.readouterr().err


def test_cli_rebuild_mentions_is_a_dry_run_unless_apply(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    c = db.connect(path)
    db.init_db(c)
    topic_id = topic(c, "dark-mode", "Dark mode")
    db.increment_topic_weekly_mentions(c, topic_id, "2026-W30", amount=3)
    c.commit()
    c.close()

    registry.main(["--db-path", path, "rebuild-mentions"])
    assert "would change 1 topic" in capsys.readouterr().out
    c = db.connect(path)
    assert db.get_topic_weekly_mentions(c, topic_id) == {"2026-W30": 3}
    c.close()

    registry.main(["--db-path", path, "rebuild-mentions", "--apply"])
    c = db.connect(path)
    assert db.get_topic_weekly_mentions(c, topic_id) == {}
```

Append to `tests/test_match.py`:

```python
def test_match_call_records_the_registry_fingerprint(tmp_path):
    conn = seed_db(tmp_path)
    topic_id = seed_existing_topic(conn)
    seed_candidate(conn, "dark mode again")
    expected = registry.registry_fingerprint(db.get_canonical_topics(conn))
    conn.close()

    match.run(db_path=str(tmp_path / "pi_agent.db"), today=date(2026, 8, 15),
              client=FakeClient([json.dumps([{"index": 0, "matched_topic_id": topic_id}])]))

    conn = db.connect(str(tmp_path / "pi_agent.db"))
    meta = json.loads(conn.execute("SELECT request_meta FROM llm_call WHERE stage = 'match'").fetchone()[0])
    assert meta["registry_fingerprint"] == expected
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_registry.py tests/test_match.py -v`
Expected: FAIL (`AttributeError: module 'registry' has no attribute 'registry_as_of'`).

- [ ] **Step 3: Add the views and the CLI to `registry.py`**

Add to the imports: `import argparse`, `import json`, `import sys`, `from datetime import date`, `from fingerprints import fingerprint`, `from weekutil import iso_week_string`. Then:

```python
def registry_as_of(conn, week):
    """The registry as it stood during `week`: topics that existed then and were not yet merged
    away, each with the definition that was valid that week."""
    merged_in_week = {r["from_topic_id"]: r["week"] for r in conn.execute(
        "SELECT from_topic_id, week FROM topic_lineage WHERE kind = 'merge'")}
    topics = []
    for topic in db.get_canonical_topics(conn, include_merged=True):
        if merged_in_week.get(topic["topic_id"], "9999-W99") <= week:
            continue
        version = db.get_topic_version_as_of(conn, topic["topic_id"], week)
        if version is None:
            continue
        topics.append({"topic_id": topic["topic_id"], "slug": topic["slug"], "name": version["name"],
                       "description": version["description"], "aliases": version["aliases"]})
    return topics


def registry_fingerprint(topics):
    return fingerprint(sorted(
        [t["topic_id"], t["name"], t["description"], list(t.get("aliases") or [])] for t in topics
    ))


def topic_history(conn, slug):
    topic = db.get_canonical_topic_by_slug(conn, slug)
    if topic is None:
        raise RegistryError(f"no topic with slug {slug!r}")
    return {
        "topic": topic,
        "versions": db.get_topic_versions(conn, topic["topic_id"]),
        "lineage": db.get_lineage_for_topic(conn, topic["topic_id"]),
        "weekly_mentions": db.get_topic_weekly_mentions(conn, topic["topic_id"]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect and edit the canonical topic registry.")
    parser.add_argument("--db-path", default="data/pi_agent.db")
    sub = parser.add_subparsers(dest="command", required=True)

    history = sub.add_parser("history", help="versions, merges/splits and weekly mentions of a topic")
    history.add_argument("slug")

    merge = sub.add_parser("merge", help="retire FROM into INTO")
    merge.add_argument("from_slug")
    merge.add_argument("into_slug")
    merge.add_argument("--reason", required=True)
    merge.add_argument("--by", default="human:cli")

    split = sub.add_parser("split", help="move some candidates of SLUG into a new topic")
    split.add_argument("slug")
    split.add_argument("--new-name", required=True)
    split.add_argument("--new-slug", required=True)
    split.add_argument("--description", required=True)
    split.add_argument("--candidates", required=True, help="comma-separated candidate ids, e.g. SC-2026-00012,SC-2026-00031")
    split.add_argument("--reason", required=True)
    split.add_argument("--by", default="human:cli")

    snapshot = sub.add_parser("snapshot", help="the registry as it stood in a given ISO week")
    snapshot.add_argument("--week", required=True, help="e.g. 2026-W40")

    rebuild = sub.add_parser("rebuild-mentions", help="recompute weekly mention counts from evidence")
    rebuild.add_argument("--apply", action="store_true", help="write the new counts (default: show what would change)")

    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    conn = db.connect(args.db_path)
    db.init_db(conn)
    week = iso_week_string(date.today())
    try:
        if args.command == "history":
            print(json.dumps(topic_history(conn, args.slug), indent=2))
        elif args.command == "merge":
            result = merge_topics(conn, args.from_slug, args.into_slug, week=week, reason=args.reason, decided_by=args.by)
            conn.commit()
            print(json.dumps(result, indent=2))
        elif args.command == "split":
            ids = [c.strip() for c in args.candidates.split(",") if c.strip()]
            result = split_topic(conn, args.slug, new_name=args.new_name, new_slug=args.new_slug,
                                 new_description=args.description, candidate_ids=ids, week=week,
                                 reason=args.reason, decided_by=args.by)
            conn.commit()
            print(json.dumps(result, indent=2))
        elif args.command == "snapshot":
            print(json.dumps(registry_as_of(conn, args.week), indent=2))
        elif args.command == "rebuild-mentions":
            diffs = db.rebuild_all_topic_weekly_mentions(conn)
            for topic_id, (before, after) in diffs.items():
                print(f"{topic_id}: {before} -> {after}")
            if args.apply:
                conn.commit()
                print(f"changed {len(diffs)} topic(s)")
            else:
                conn.rollback()
                print(f"would change {len(diffs)} topic(s); rerun with --apply to write")
    except RegistryError as e:
        conn.rollback()
        print(e, file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Record the registry fingerprint on match calls**

In `match.py`, add `import registry` to the imports. In `match_batch`, add to `request_meta`:

```python
            "registry_fingerprint": registry.registry_fingerprint(existing),
```

`registry` imports `match` only for `normalize_slug` and `candidate_week`, which are used inside functions. Python resolves the circular import because neither module uses the other at import time. If ruff or a test reports an import-order problem, move `import match` inside `split_topic` in `registry.py`.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_registry.py tests/test_match.py -v`
Expected: PASS.

- [ ] **Step 6: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add registry.py match.py tests/test_registry.py tests/test_match.py
git commit -m "feat(registry): as-of snapshots, topic history, registry CLI, fingerprint on match calls"
```

---

### Task 5: Export a production week as a draft matching scenario

**Files:**
- Modify: `evals/harness.py` (`_load_matching_file` rejects `draft: true`)
- Create: `evals/export_matching_scenario.py`
- Test: `tests/test_export_matching_scenario.py` (create), `tests/test_evals_harness.py` (append)

**Interfaces:**
- Consumes: `registry.registry_as_of` (Task 4); `original_topic_id` (Task 1); `harness.load_golden_dir`.
- Produces:
  - `export_matching_scenario.build_scenario(conn, week, limit) -> dict` (golden matching format plus `draft: true`)
  - `export_matching_scenario.main(argv=None) -> int`
  - Loader error: `draft scenario: review every expected decision, then delete 'draft: true'`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_export_matching_scenario.py`:

```python
import yaml

import db
from evals import export_matching_scenario, harness


def seed(path):
    conn = db.connect(path)
    db.init_db(conn)
    old = db.insert_canonical_topic(conn, slug="dark-mode", name="Dark mode", description="wants dark theme",
                                    aliases=[], first_seen="2026-W30", last_seen="2026-W30")
    new = db.insert_canonical_topic(conn, slug="csv", name="CSV export", description="wants CSV",
                                    aliases=[], first_seen="2026-W33", last_seen="2026-W33")
    for i, (topic_id, captured) in enumerate([(old, "2026-08-15"), (new, "2026-08-15"), (old, "2026-07-27")]):
        evidence_id = db.insert_evidence(conn, source_type="reddit_post", source_name="s", source_url=f"/{i}",
                                         captured_at=f"{captured}T00:00:00+00:00", published_at=f"{captured}T00:00:00+00:00",
                                         title="t", content="c", metadata={})
        db.insert_signal_candidate(conn, evidence_id=evidence_id, signal_type="new_feature_demand",
                                   summary=f"summary {i}", confidence=0.9, topic_id=topic_id)
    conn.commit()
    conn.close()
    return old


def test_build_scenario_uses_the_previous_weeks_registry_and_this_weeks_candidates(tmp_path):
    path = str(tmp_path / "pi.db")
    old = seed(path)
    conn = db.connect(path)

    scenario = export_matching_scenario.build_scenario(conn, "2026-W33", limit=30)

    assert scenario["draft"] is True
    assert scenario["name"] == "prod_2026_W33"
    assert [t["topic_id"] for t in scenario["existing_topics"]] == [old]
    assert [c["summary"] for c in scenario["candidates"]] == ["summary 0", "summary 1"]
    assert scenario["expected"] == [
        {"index": 0, "matched_topic_id": old, "new_topic": None},
        {"index": 1, "matched_topic_id": None, "new_topic": None},
    ]


def test_main_writes_a_file_the_loader_refuses_until_draft_is_removed(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    seed(path)
    out_dir = tmp_path / "golden"
    out_dir.mkdir()
    out = out_dir / "prod.yaml"

    assert export_matching_scenario.main(["--db-path", path, "--week", "2026-W33", "--out", str(out)]) == 0

    try:
        harness.load_golden_dir(out_dir, "matching")
        raise AssertionError("draft scenario should be rejected")
    except harness.GoldenSchemaError as e:
        assert "draft scenario" in str(e)

    data = yaml.safe_load(out.read_text(encoding="utf-8"))
    del data["draft"]
    out.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    [scenario] = harness.load_golden_dir(out_dir, "matching")
    assert scenario.name == "prod_2026_W33"


def test_main_refuses_a_week_with_no_matched_candidates(tmp_path, capsys):
    path = str(tmp_path / "pi.db")
    seed(path)
    assert export_matching_scenario.main(["--db-path", path, "--week", "2026-W40",
                                          "--out", str(tmp_path / "x.yaml")]) == 2
    assert "no matched candidates" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_export_matching_scenario.py -v`
Expected: FAIL (`ImportError: cannot import name 'export_matching_scenario'`).

- [ ] **Step 3: Reject drafts in the harness**

In `evals/harness.py`, `_load_matching_file`, right after `name = _require_str(file_path, None, data, "name")`:

```python
    if data.get("draft"):
        _fail(file_path, name, "draft scenario: review every expected decision, then delete 'draft: true'")
```

- [ ] **Step 4: Create `evals/export_matching_scenario.py`**

```python
"""Draft a matching-eval scenario from one production week.

Usage: python evals/export_matching_scenario.py --week 2026-W40 [--db-path data/pi_agent.db] [--limit 30] [--out PATH] [--force]

existing_topics = the registry as it stood the week before; candidates = that week's matched
candidates; expected = where production first put each one (original_topic_id), or a new topic
when that topic did not exist yet. Production is not ground truth: the file is written with
`draft: true`, which the golden loader refuses until a human has checked every decision and
deleted that line. Summaries come from real posts; read them before committing. Makes no API calls.
"""
import argparse
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

import db  # noqa: E402
import match  # noqa: E402
import registry  # noqa: E402
from evals import runs  # noqa: E402
from weekutil import iso_week_string  # noqa: E402

HEADER = """\
# DRAFT matching scenario exported from production. Check every expected decision: production
# is what the matcher did, not what is correct. Fix wrong ones, add notes, then delete the
# `draft: true` line and move the file into evals/golden/matching/.
"""


def previous_week(week):
    year, number = week.split("-W")
    monday = datetime.date.fromisocalendar(int(year), int(number), 1)
    return iso_week_string(monday - datetime.timedelta(days=7))


def build_scenario(conn, week, limit):
    topics = registry.registry_as_of(conn, previous_week(week))
    known = {t["topic_id"] for t in topics}
    rows = conn.execute(
        "SELECT sc.candidate_id, sc.signal_type, sc.summary, sc.original_topic_id, e.captured_at "
        "FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id "
        "WHERE sc.original_topic_id IS NOT NULL AND e.duplicate_of IS NULL ORDER BY sc.candidate_id"
    ).fetchall()
    chosen = [dict(r) for r in rows if match.candidate_week(dict(r), None) == week][:limit]
    return {
        "name": f"prod_{week.replace('-', '_')}",
        "draft": True,
        "existing_topics": [{"topic_id": t["topic_id"], "name": t["name"], "description": t["description"]}
                            for t in topics],
        "candidates": [{"signal_type": c["signal_type"], "summary": c["summary"]} for c in chosen],
        "expected": [{"index": i, "matched_topic_id": c["original_topic_id"] if c["original_topic_id"] in known else None,
                      "new_topic": None} for i, c in enumerate(chosen)],
        "notes": f"Exported from production week {week}; candidate ids {[c['candidate_id'] for c in chosen]}.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Draft a matching scenario from one production week.")
    parser.add_argument("--week", required=True, help="ISO week, e.g. 2026-W40")
    parser.add_argument("--db-path", default="data/pi_agent.db")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--out", help="default: evals/results/matching_scenario_<week>.yaml")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    conn = db.connect(args.db_path)
    db.init_db(conn)
    try:
        scenario = build_scenario(conn, args.week, args.limit)
    finally:
        conn.close()
    if not scenario["candidates"]:
        print(f"no matched candidates captured in {args.week}", file=sys.stderr)
        return 2

    out = Path(args.out or runs.DEFAULT_RESULTS_DIR / f"matching_scenario_{args.week}.yaml")
    if out.exists() and not args.force:
        print(f"{out} already exists; pass --force to overwrite it", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HEADER + yaml.safe_dump(scenario, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    print(f"wrote draft scenario with {len(scenario['candidates'])} candidates to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_export_matching_scenario.py tests/test_evals_golden.py tests/test_evals_harness.py -v`
Expected: PASS.

- [ ] **Step 6: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add evals/harness.py evals/export_matching_scenario.py tests/test_export_matching_scenario.py
git commit -m "feat(evals): draft matching scenarios from production weeks; loader refuses unreviewed drafts"
```

---

### Task 6: ADR 006, README, and rebuilding real counts

**Files:**
- Create: `docs/decisions/006-versioned-registry-and-derived-mentions.md`
- Modify: `README.md`

- [ ] **Step 1: Write ADR 006**

```markdown
# Decision

Keep the registry's history and derive mention counts from evidence:

- every topic definition change is a `topic_version` row;
- merges and splits are explicit human operations recorded in `topic_lineage`;
- each candidate keeps its `original_topic_id`;
- `topic_weekly_mentions` is a cache rebuilt per topic from non-duplicate candidates, keyed by the ISO week of `captured_at`.

## Context

The README names split and merged topics as the main failure mode of matching, but there was no way to fix one: no merge, no split, no history. Weekly counts were incremented by the week the match stage ran, so a late run put mentions in the wrong week, and counts could drift from the candidates they summarize.

## Options considered

- **Mutable registry plus a manual SQL fix.** Rejected: loses history, error-prone, not reproducible.
- **Full event sourcing of the registry.** Rejected: rebuilding the registry on every read is more machinery than one SQLite file needs.
- **Latest view plus append-only history tables** (chosen): `canonical_topic` stays the "latest" view, so reports, materiality and query don't change. History lives beside it.

## Consequences

- `python registry.py merge|split|history|snapshot|rebuild-mentions`. Merges and splits are human decisions. P5's review queue can propose them but never applies them on its own.
- Reports keep using the latest view. `registry.py snapshot --week W` and the scenario export use the as-of view.
- Counts can always be recomputed (`rebuild-mentions`). Rebuilding re-keys counts that older code keyed by run week.
- The matcher sees aliases (former names of merged topics) and follows merged ids, so stored or stale decisions land on the surviving topic.
```

- [ ] **Step 2: Update `README.md`**

1. **Architecture:** add `registry.py`: "merge, split, history and as-of snapshots of the canonical topic registry (human operations)".
2. **Safety / trust model:** under "Requires review before acting on it", replace the "newly created canonical topics" bullet with:

   > newly created canonical topics. Fix a split or merge with `python registry.py merge FROM INTO --reason ...` or `python registry.py split SLUG --new-name ... --candidates ...`; history is kept (`python registry.py history SLUG`).
3. **Evals:** add a paragraph:

   > **Scenarios from production.** `python evals/export_matching_scenario.py --week 2026-W40` drafts a matching scenario from one real week: the registry as it stood the week before, plus that week's candidates and where production put them. Drafts carry `draft: true`, which the loader refuses; check every decision, then delete that line.
4. **Limitations:** remove nothing. Add: "**Reports use the latest registry.** After a merge, past weeks are reported under the surviving topic. Use `registry.py snapshot --week W` for the registry as it was."

- [ ] **Step 3: Run the dry-run rebuild on a copy of the real database (manual)**

```powershell
Copy-Item data\pi_agent.db data\pi_agent.backup.db
python registry.py rebuild-mentions
```

Paste the output into the PR. Run `python registry.py rebuild-mentions --apply` only if you, the owner, accept the re-keyed counts (spec §6 item 2).

- [ ] **Step 4: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add docs/decisions/006-versioned-registry-and-derived-mentions.md README.md
git commit -m "docs: ADR 006 versioned registry and derived mentions"
```

---

## Self-review notes

- **Spec coverage:**
  - P3 (history, merge/split): Tasks 1, 3, 4. P4 (run-week attribution): Task 2.
  - Spec P2 decision 5 (production → scenarios): Task 5.
- **Interfaces used by P5:** `registry.merge_topics(conn, from_slug, into_slug, *, week, reason, decided_by)`, `registry.RegistryError`, `db.get_canonical_topics(conn, include_merged=False)`.
- **Changes to P1 code:**
  - `apply_resolutions` now refreshes counts instead of incrementing, and uses evidence weeks.
  - `resolve_decisions` gains `redirects`.
  - `set_candidate_topic` also sets `original_topic_id`.
  - The P1 tests still pass because their candidates are captured in the week they assert.
