# Signal Candidate Entity & Effective Date Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add nullable `entity` and `effective_date` fields to `signal_candidate`, all the way
from the SQLite schema through `extract.py`'s LLM extraction, so future non-Reddit sources
(competitor changelogs, in particular) have somewhere to put "Competitor X ends support for
Product Y on 2027-05-31" instead of only a topic-shaped entity.

**Architecture:** `signal_candidate` gains two nullable columns (`entity` as JSON text,
`effective_date` as an ISO date string). `db.insert_signal_candidate` accepts them as optional
kwargs; `db.get_candidates_without_topic` decodes `entity` back to a dict on read, matching the
existing JSON-column pattern used for `evidence.metadata` and `material_signal.entity`.
`extract.py`'s prompt asks the model for `entity`/`effective_date` as optional fields (null when
the source text doesn't name one), and `extract.run()` passes whatever comes back straight through
to the DB.

**Tech Stack:** Python, SQLite (`db.py`), Anthropic Python SDK (`extract.py`), pytest.

**Spec:** [docs/product-intelligence-agent-vision.md](../../product-intelligence-agent-vision.md)
§4.2 (extracted signal candidate) and
[docs/product-intelligence-agent-gap-analysis.md](../../product-intelligence-agent-gap-analysis.md)
(gap #1, Phase 0).

## Global Constraints

- Evidence rows stay immutable; this plan only touches `signal_candidate` (never `evidence`).
- One LLM call per evidence row for extraction — do not turn `extract.py` into a multi-call or
  agentic loop (ADR 001: "two narrow LLM calls, not an agent").
- No migration script: `db.py` has no `ALTER TABLE`/migration machinery anywhere in the codebase
  today, and `data/` is gitignored (local/dev DB only). Editing `SCHEMA_SQL` directly is the
  established pattern (see ADR 002) — an existing local `data/pi_agent.db` predating this change
  should just be deleted and rebuilt by re-running the pipeline, not migrated in place.
- Do **not** wire `entity`/`effective_date` into `match.py` or `materiality.py` in this plan.
  Topic matching stays signal_type/summary-based, and `materiality.py::build_material_signal`
  keeps synthesizing its own `customer_topic`-shaped entity for now. There is no real
  entity-bearing source yet (that's Phase 3 in the gap-analysis roadmap) — designing how
  `materiality.py` should pick an entity from possibly-conflicting per-candidate entities without
  a real multi-entity dataset to test against would be speculative. Only the storage/extraction
  plumbing is in scope here.
- Every stage's `run()` keeps its existing transactional `try/except: conn.rollback()` shape —
  don't restructure it.
- Run the full suite with `pytest -v` from the repo root before each commit that isn't itself
  mid-cycle (i.e. after each task's final step).

---

### Task 1: `signal_candidate` schema + `db.py` read/write

**Files:**
- Modify: [db.py](../../../db.py) (`SCHEMA_SQL` lines 29-36, `insert_signal_candidate` lines
  136-147, `get_candidates_without_topic` lines 154-158)
- Test: [tests/test_db.py](../../../tests/test_db.py)

**Interfaces:**
- Produces: `db.insert_signal_candidate(conn, *, evidence_id, signal_type, summary, confidence, topic_id=None, entity=None, effective_date=None) -> candidate_id: str`. `entity` is a `dict` or `None`; `effective_date` is a `str` (e.g. `"2027-05-31"`) or `None`.
- Produces: `db.get_candidates_without_topic(conn) -> list[dict]`. Each dict now additionally has `"entity"` (`dict` or `None`, JSON-decoded) and `"effective_date"` (`str` or `None`).
- Produces: `db._signal_candidate_row_to_dict(row) -> dict` (internal helper, same pattern as `_evidence_row_to_dict`/`_topic_row_to_dict`/`_material_signal_row_to_dict`).

- [ ] **Step 1: Write the failing tests for `insert_signal_candidate`**

Add to the top of `tests/test_db.py`:

```python
import json
import sqlite3
```

(replacing the existing `import sqlite3` line with both imports — `json` isn't imported yet).

Add these two tests after `test_insert_signal_candidate_rejects_unknown_evidence_id`:

```python
def test_insert_signal_candidate_stores_entity_and_effective_date_when_provided(conn):
    evidence_id = _insert_sample_evidence(conn)

    candidate_id = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand", summary="s", confidence=0.9,
        entity={"type": "competitor_product", "company": "Competitor X", "product": "Product Y"},
        effective_date="2027-05-31",
    )

    row = conn.execute(
        "SELECT entity, effective_date FROM signal_candidate WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    assert json.loads(row["entity"]) == {
        "type": "competitor_product", "company": "Competitor X", "product": "Product Y",
    }
    assert row["effective_date"] == "2027-05-31"


def test_insert_signal_candidate_defaults_entity_and_effective_date_to_none(conn):
    evidence_id = _insert_sample_evidence(conn)

    candidate_id = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand", summary="s", confidence=0.9,
    )

    row = conn.execute(
        "SELECT entity, effective_date FROM signal_candidate WHERE candidate_id = ?", (candidate_id,)
    ).fetchone()
    assert row["entity"] is None
    assert row["effective_date"] is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_db.py -v -k "entity_and_effective_date"`
Expected: both FAIL with `sqlite3.OperationalError: table signal_candidate has no column named entity`
(from `insert_signal_candidate`'s `conn.execute`, once you try to pass the kwarg — actually, since
the kwargs don't exist on the function yet, expect `TypeError: insert_signal_candidate() got an
unexpected keyword argument 'entity'` for the first test, and the second test passes vacuously
since it doesn't pass those kwargs. Confirm the first test fails with that `TypeError` before
proceeding.)

- [ ] **Step 3: Add the columns and accept/store the new fields**

In `db.py`, change the `signal_candidate` table in `SCHEMA_SQL`:

```python
CREATE TABLE IF NOT EXISTS signal_candidate (
  candidate_id TEXT PRIMARY KEY,
  evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
  signal_type TEXT NOT NULL,
  topic_id TEXT REFERENCES canonical_topic(topic_id),
  summary TEXT NOT NULL,
  confidence REAL NOT NULL,
  entity TEXT,
  effective_date TEXT
);
```

Replace `insert_signal_candidate`:

```python
def insert_signal_candidate(conn, *, evidence_id, signal_type, summary, confidence, topic_id=None,
                             entity=None, effective_date=None):
    evidence = get_evidence(conn, evidence_id)
    if evidence is None:
        raise DBError(f"cannot create signal_candidate: evidence {evidence_id} does not exist")
    year = evidence["captured_at"][:4]
    candidate_id = _next_sequence_id(conn, "signal_candidate", "candidate_id", f"SC-{year}-", 5)
    conn.execute(
        "INSERT INTO signal_candidate (candidate_id, evidence_id, signal_type, topic_id, summary, "
        "confidence, entity, effective_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (candidate_id, evidence_id, signal_type, topic_id, summary, confidence,
         json.dumps(entity, sort_keys=True) if entity is not None else None, effective_date),
    )
    return candidate_id
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_db.py -v -k "entity_and_effective_date"`
Expected: both PASS

- [ ] **Step 5: Write the failing test for `get_candidates_without_topic`**

Add after `test_get_evidence_without_candidate_excludes_processed_rows`:

```python
def test_get_candidates_without_topic_round_trips_entity_and_effective_date(conn):
    evidence_id = _insert_sample_evidence(conn)
    db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="product_end_of_support", summary="s", confidence=0.95,
        entity={"type": "competitor_product", "company": "Competitor X", "product": "Product Y"},
        effective_date="2027-05-31",
    )
    db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand", summary="s2", confidence=0.8,
    )

    candidates = db.get_candidates_without_topic(conn)

    assert candidates[0]["entity"] == {
        "type": "competitor_product", "company": "Competitor X", "product": "Product Y",
    }
    assert candidates[0]["effective_date"] == "2027-05-31"
    assert candidates[1]["entity"] is None
    assert candidates[1]["effective_date"] is None
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `pytest tests/test_db.py -v -k round_trips_entity_and_effective_date`
Expected: FAIL — `candidates[0]["entity"]` is currently the raw JSON string, not a decoded dict, so
the equality assertion fails.

- [ ] **Step 7: Add the row-decoding helper and use it in `get_candidates_without_topic`**

Add near the other `_*_row_to_dict` helpers (after `_topic_row_to_dict`):

```python
def _signal_candidate_row_to_dict(row):
    d = dict(row)
    d["entity"] = json.loads(d["entity"]) if d["entity"] is not None else None
    return d
```

Replace `get_candidates_without_topic`:

```python
def get_candidates_without_topic(conn):
    rows = conn.execute(
        "SELECT * FROM signal_candidate WHERE topic_id IS NULL ORDER BY candidate_id"
    ).fetchall()
    return [_signal_candidate_row_to_dict(r) for r in rows]
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `pytest tests/test_db.py -v -k round_trips_entity_and_effective_date`
Expected: PASS

- [ ] **Step 9: Run the full `test_db.py` file and commit**

Run: `pytest tests/test_db.py -v`
Expected: all PASS (existing tests must be unaffected — `entity`/`effective_date` are additive,
nullable columns).

```bash
git add db.py tests/test_db.py
git commit -m "feat: add entity and effective_date fields to signal_candidate"
```

---

### Task 2: `extract.py` optional entity/effective_date extraction

**Files:**
- Modify: [extract.py](../../../extract.py) (`EXTRACTION_PROMPT_TEMPLATE` lines 25-38,
  `extract_topic` lines 52-80, `run` lines 83-110)
- Test: [tests/test_extract.py](../../../tests/test_extract.py)

**Interfaces:**
- Consumes: `db.insert_signal_candidate(conn, *, evidence_id, signal_type, summary, confidence, topic_id=None, entity=None, effective_date=None)` from Task 1.
- Produces: `extract.extract_topic(client, evidence: dict) -> dict | None`. On a non-skip response, the returned dict now has keys `signal_type`, `summary`, `confidence`, `entity` (`dict | None`), `effective_date` (`str | None`) — `entity`/`effective_date` default to `None` when the model omits them (they are not in the required-keys check).

- [ ] **Step 1: Write/update the failing tests**

In `tests/test_extract.py`, replace `test_extract_topic_parses_valid_response`:

```python
def test_extract_topic_parses_valid_response():
    client = FakeClient([
        json.dumps({"signal_type": "new_feature_demand", "summary": "User wants a dark theme", "confidence": 0.9})
    ])

    result = extract.extract_topic(client, SAMPLE_EVIDENCE)

    assert result == {
        "signal_type": "new_feature_demand", "summary": "User wants a dark theme", "confidence": 0.9,
        "entity": None, "effective_date": None,
    }
```

Add a new test directly after it:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_extract.py -v -k "parses_valid_response or entity_and_effective_date_when_present"`
Expected: both FAIL — `extract_topic`'s current return dict has no `entity`/`effective_date` keys,
so the first assertion's dict equality fails and the second's `result["entity"]` raises `KeyError`.

- [ ] **Step 3: Update the prompt and `extract_topic`**

Replace `EXTRACTION_PROMPT_TEMPLATE`:

```python
EXTRACTION_PROMPT_TEMPLATE = """You are analyzing a Reddit post about a software product for product \
feedback signal extraction.

Post title: {title}
Post body: {content}

Respond with ONLY a JSON object with these exact keys:
- "signal_type": one of {signal_types}
- "summary": a one-line description of what's being said
- "confidence": a number between 0.0 and 1.0 for how confident you are in this classification
- "entity": an object {{"type": ..., "company": ..., "product": ...}} identifying the specific \
company or product this signal is about, or null if the post does not name one
- "effective_date": an ISO date ("YYYY-MM-DD") if the post states a specific effective date for a \
change (e.g. an end-of-support date), or null otherwise

If the post is not meaningful product feedback (spam, off-topic, low-effort, or clearly \
AI-generated filler), respond with exactly: {{"skip": true}}
"""
```

Replace the return statement at the end of `extract_topic` (the `missing` check and
`signal_type` validation stay exactly as they are — `entity`/`effective_date` stay optional and
are not part of the required-keys check):

```python
    return {
        "signal_type": parsed["signal_type"],
        "summary": parsed["summary"],
        "confidence": parsed["confidence"],
        "entity": parsed.get("entity"),
        "effective_date": parsed.get("effective_date"),
    }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_extract.py -v -k "parses_valid_response or entity_and_effective_date_when_present"`
Expected: both PASS

- [ ] **Step 5: Write the failing test for `run()` persisting the new fields**

Add after `test_run_creates_candidates_for_pending_evidence_and_skips_bad_ones`:

```python
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
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `pytest tests/test_extract.py -v -k persists_entity_and_effective_date`
Expected: FAIL — `run()` doesn't pass `entity`/`effective_date` to `db.insert_signal_candidate` yet,
so both columns are `NULL` and the assertions fail.

- [ ] **Step 7: Pass the new fields through in `run()`**

In `extract.py`'s `run()`, replace the `db.insert_signal_candidate` call:

```python
            if result is not None:
                db.insert_signal_candidate(
                    conn,
                    evidence_id=evidence["evidence_id"],
                    signal_type=result["signal_type"],
                    summary=result["summary"],
                    confidence=result["confidence"],
                    entity=result["entity"],
                    effective_date=result["effective_date"],
                )
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `pytest tests/test_extract.py -v -k persists_entity_and_effective_date`
Expected: PASS

- [ ] **Step 9: Run the full suite**

Run: `pytest -v`
Expected: all tests PASS, including `tests/test_end_to_end.py` (its fixed LLM responses omit
`entity`/`effective_date`, which now default to `None` — no change in behavior for the existing
Reddit-only pipeline).

- [ ] **Step 10: Note the new fields in the README pipeline description**

In `README.md`, update this bullet (around line 100-102):

```markdown
- **extract.py** — classifies each not-yet-processed evidence row into a `signal_type`
  (from the product intelligence signal taxonomy) plus a confidence score, storing it as
  a `signal_candidate`.
```

to:

```markdown
- **extract.py** — classifies each not-yet-processed evidence row into a `signal_type`
  (from the product intelligence signal taxonomy) plus a confidence score, storing it as
  a `signal_candidate`. Also captures an optional `entity` and `effective_date` when the
  source text names one (currently unused downstream — Reddit posts rarely name one, and
  no source that would need it, like competitor changelogs, exists yet).
```

- [ ] **Step 11: Commit**

```bash
git add extract.py tests/test_extract.py README.md
git commit -m "feat: extract optional entity and effective_date from evidence"
```

---

## Self-Review

**Spec coverage:** §4.2's `entity`/`effective_date` fields are now storable end-to-end from
extraction through to the DB (Task 1 + Task 2). §4.2's `evidence_ids` (plural, spec allows a
candidate to cite multiple evidence rows) is intentionally *not* addressed here — the codebase's
1:1 evidence→candidate model is an existing, working design choice (ADR-adjacent, see README's "AI
design decisions"), not part of the entity/effective_date gap this plan targets, and changing
cardinality is a separate, larger decision outside Phase 0's scope per the gap analysis.

**Placeholder scan:** no TBD/TODO/"add error handling"-style steps; every step has concrete code
or an exact command.

**Type consistency:** `entity` is `dict | None` everywhere it appears (DB param, `extract_topic`
return value, decoded row). `effective_date` is `str | None` everywhere. `db.insert_signal_candidate`'s
new kwargs (`entity`, `effective_date`) match the field names `extract.run()` reads off
`extract_topic`'s result dict exactly.
