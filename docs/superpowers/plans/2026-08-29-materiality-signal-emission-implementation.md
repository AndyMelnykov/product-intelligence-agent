# Materiality & Signal Emission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add materiality classification and the `product_intelligence.signal.material` event contract on top of the existing evidence/candidate/topic/trend pipeline, so a topic that crosses a materiality threshold produces a durable `material_signal` row and an emitted event — closing vision-doc sections §8, §9, and §10. Also retire the now-dead `registry.py`/`migrate_to_sqlite.py` one-time-migration code.

**Architecture:** A fifth pipeline stage, `materiality.py`, runs after `report.py` inside `run_weekly.py`. It reuses `report.compute_trends` for trend classification (no duplicated math), adds one new SQLite table (`material_signal`), and emits events as JSON lines appended to `data/events.jsonl` (no network call — there is no real downstream service yet). Classification is fully deterministic (no LLM call), split into an event-driven path (some signal types are material on a single high-confidence occurrence) and a trend-driven path (customer-market signal types need a rising trend plus volume/confidence).

**Tech Stack:** Python 3.11+, stdlib `sqlite3` + `json` (no new dependency), pytest.

**Spec:** [docs/superpowers/specs/2026-08-29-materiality-signal-emission-design.md](../specs/2026-08-29-materiality-signal-emission-design.md) (references the north-star vision doc at [docs/product-intelligence-agent-vision.md](../../product-intelligence-agent-vision.md))

## Global Constraints

- No new third-party dependency — `materiality.py` uses only `db.py`, `report.py`, `weekutil.py`, and the stdlib.
- `materiality.run()` wraps its DB writes in one transaction per invocation: commit only on full success, `conn.rollback()` and re-raise on any unhandled exception — same pattern as every other stage.
- Events are appended to `data/events.jsonl` **only after** the DB transaction has committed, so a crash never emits an event for data that got rolled back, and never leaves a `material_signal` row with no corresponding event.
- `materiality.run()` only evaluates a topic when it has at least one `signal_candidate` whose linked evidence was captured in the current ISO week — a topic with no new evidence this run is skipped, not re-assessed.
- `low`/`medium` materiality never creates a `material_signal` row or emits an event (per the vision doc's own "store only" / "update topic" actions) — only `high`/`critical` do.
- `materiality_score` is a fixed representative value per label (`{"low": 0.2, "medium": 0.5, "high": 0.8, "critical": 0.95}`), not a fitted/weighted formula — the vision doc explicitly warns against "fake mathematical precision."
- `entity` on a `material_signal` is always `{"type": "customer_topic", "topic_id": ..., "topic_name": ...}` for now — there is no competitor/company entity extraction in this repo yet (see spec Non-goals).
- No `effective_date` field on the emitted event — not meaningful for aggregated customer-topic signals.
- Signal ID format: `signal_id` = `SIG-<year>-<4-digit sequence>` (year taken from `created_at`), matching the vision doc's `SIG-2026-0182` example.
- `registry.py`, `migrate_to_sqlite.py`, and their tests/fixtures are deleted in Task 1 — both are one-time-migration code whose job is done (the SQLite migration already shipped), and `migrate_to_sqlite.py`'s only import is `registry`, so they must go together or the import breaks.

---

## File Structure

```
db.py                        # MODIFY: add material_signal table + insert_material_signal/get_material_signal/get_candidates_for_topic
materiality.py                # NEW: classification, signal construction, event emission, run() orchestration
run_weekly.py                 # MODIFY: add "materiality" stage
query.py                      # MODIFY: add `signal <signal_id>` command
registry.py                   # DELETE (Task 1) — dead one-time-migration code
migrate_to_sqlite.py          # DELETE (Task 1) — dead one-time-migration code
tests/
  test_db.py                    # MODIFY: material_signal + get_candidates_for_topic tests
  test_materiality.py            # NEW
  test_run_weekly.py              # MODIFY: assert materiality stage runs
  test_query.py                   # MODIFY: signal command tests
  test_end_to_end.py              # MODIFY: add a two-week scenario that crosses the high threshold
  test_registry.py                # DELETE (Task 1)
  test_migrate_to_sqlite.py       # DELETE (Task 1)
  fixtures/
    registry_sample.json          # DELETE (Task 1) — only used by test_migrate_to_sqlite.py
```

`db.py` remains the only module that issues raw SQL. `materiality.py` calls `db.py` and `report.py`'s public functions only.

---

### Task 1: Retire `registry.py` and `migrate_to_sqlite.py`

**Files:**
- Delete: `registry.py`, `migrate_to_sqlite.py`
- Delete: `tests/test_registry.py`, `tests/test_migrate_to_sqlite.py`, `tests/fixtures/registry_sample.json`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing new — this only removes dead code. No other module imports `registry` or `migrate_to_sqlite`.

- [ ] **Step 1: Confirm nothing else imports these modules**

Run: `grep -rn "import registry\|from registry\|import migrate_to_sqlite\|from migrate_to_sqlite" --include=*.py .`
Expected: only `migrate_to_sqlite.py` (imports `registry`) and `tests/test_migrate_to_sqlite.py` (imports `migrate_to_sqlite` and uses `tests/fixtures/registry_sample.json`) — no other file references either module.

- [ ] **Step 2: Delete the files**

```bash
git rm registry.py migrate_to_sqlite.py tests/test_registry.py tests/test_migrate_to_sqlite.py tests/fixtures/registry_sample.json
```

- [ ] **Step 3: Run the full test suite to confirm nothing else depended on them**

Run: `pytest -v`
Expected: all remaining tests pass (no `ModuleNotFoundError`, no fixture-not-found errors).

- [ ] **Step 4: Commit**

```bash
git commit -m "chore: retire registry.py and migrate_to_sqlite.py (one-time migration is complete)"
```

---

### Task 2: `db.py` — `material_signal` table and CRUD

**Files:**
- Modify: `db.py`
- Modify: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing new (extends the existing `db.py` module from the evidence-data-model migration).
- Produces (used by Tasks 4-6): `db.insert_material_signal(conn, *, created_at, signal_type, topic_id, entity: dict, summary, confidence_label, materiality_label, materiality_score, materiality_reasons: list[str], evidence_ids: list[str], change_type, recommended_next_step) -> str`, `db.get_material_signal(conn, signal_id) -> dict | None`, `db.get_candidates_for_topic(conn, topic_id) -> list[dict]` (each dict has `candidate_id`, `evidence_id`, `signal_type`, `summary`, `confidence`, `captured_at`).

- [ ] **Step 1: Write the failing tests**

Modify the existing `test_init_db_creates_all_tables` test at the top of `tests/test_db.py`:

```python
def test_init_db_creates_all_tables(conn):
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"evidence", "canonical_topic", "signal_candidate", "topic_weekly_mentions", "material_signal"} <= tables
```

Add these new tests to the bottom of `tests/test_db.py`:

```python
def test_insert_material_signal_returns_sequential_ids_scoped_by_year(conn):
    evidence_id = _insert_sample_evidence(conn)
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W33",
    )

    def _insert(created_at):
        return db.insert_material_signal(
            conn, created_at=created_at, signal_type="new_feature_demand", topic_id=topic_id,
            entity={"type": "customer_topic", "topic_id": topic_id, "topic_name": "Dark mode support"},
            summary="Dark mode support: rising", confidence_label="high",
            materiality_label="high", materiality_score=0.8,
            materiality_reasons=["trend is rising with 5 mentions this week"],
            evidence_ids=[evidence_id], change_type="trend_change",
            recommended_next_step="strategic_assessment",
        )

    first = _insert("2026-08-23T14:32:00+00:00")
    second = _insert("2026-08-23T15:00:00+00:00")
    third = _insert("2027-01-05T00:00:00+00:00")

    assert first == "SIG-2026-0001"
    assert second == "SIG-2026-0002"
    assert third == "SIG-2027-0001"


def test_get_material_signal_round_trips_json_fields(conn):
    evidence_id = _insert_sample_evidence(conn)
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W33",
    )
    signal_id = db.insert_material_signal(
        conn, created_at="2026-08-23T14:32:00+00:00", signal_type="new_feature_demand", topic_id=topic_id,
        entity={"type": "customer_topic", "topic_id": topic_id, "topic_name": "Dark mode support"},
        summary="Dark mode support: rising", confidence_label="high",
        materiality_label="high", materiality_score=0.8,
        materiality_reasons=["trend is rising with 5 mentions this week"],
        evidence_ids=[evidence_id], change_type="trend_change",
        recommended_next_step="strategic_assessment",
    )

    signal = db.get_material_signal(conn, signal_id)

    assert signal["signal_id"] == signal_id
    assert signal["entity"] == {"type": "customer_topic", "topic_id": topic_id, "topic_name": "Dark mode support"}
    assert signal["materiality_reasons"] == ["trend is rising with 5 mentions this week"]
    assert signal["evidence_ids"] == [evidence_id]
    assert signal["materiality_label"] == "high"


def test_get_material_signal_returns_none_for_unknown_id(conn):
    assert db.get_material_signal(conn, "SIG-2026-9999") is None


def test_get_candidates_for_topic_returns_joined_rows_ordered_by_candidate_id(conn):
    evidence_id = _insert_sample_evidence(conn, captured_at="2026-08-15T00:00:00+00:00")
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W33", last_seen="2026-W33",
    )
    candidate_id = db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand",
        summary="User wants dark mode", confidence=0.9, topic_id=topic_id,
    )

    candidates = db.get_candidates_for_topic(conn, topic_id)

    assert candidates == [{
        "candidate_id": candidate_id, "evidence_id": evidence_id, "signal_type": "new_feature_demand",
        "summary": "User wants dark mode", "confidence": 0.9, "captured_at": "2026-08-15T00:00:00+00:00",
    }]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_db.py -v`
Expected: FAIL — `material_signal` table doesn't exist, `insert_material_signal`/`get_material_signal`/`get_candidates_for_topic` don't exist.

- [ ] **Step 3: Implement the changes in `db.py`**

Add to `SCHEMA_SQL` (inside the existing triple-quoted string, after the `topic_weekly_mentions` table):

```sql
CREATE TABLE IF NOT EXISTS material_signal (
  signal_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  signal_type TEXT NOT NULL,
  topic_id TEXT REFERENCES canonical_topic(topic_id),
  entity TEXT NOT NULL,
  summary TEXT NOT NULL,
  confidence_label TEXT NOT NULL,
  materiality_label TEXT NOT NULL,
  materiality_score REAL NOT NULL,
  materiality_reasons TEXT NOT NULL,
  evidence_ids TEXT NOT NULL,
  change_type TEXT NOT NULL,
  recommended_next_step TEXT NOT NULL
);
```

Add these functions to the bottom of `db.py`:

```python
def _material_signal_row_to_dict(row):
    d = dict(row)
    d["entity"] = json.loads(d["entity"])
    d["materiality_reasons"] = json.loads(d["materiality_reasons"])
    d["evidence_ids"] = json.loads(d["evidence_ids"])
    return d


def insert_material_signal(conn, *, created_at, signal_type, topic_id, entity, summary, confidence_label,
                            materiality_label, materiality_score, materiality_reasons, evidence_ids,
                            change_type, recommended_next_step):
    year = created_at[:4]
    signal_id = _next_sequence_id(conn, "material_signal", "signal_id", f"SIG-{year}-", 4)
    conn.execute(
        "INSERT INTO material_signal (signal_id, created_at, signal_type, topic_id, entity, summary, "
        "confidence_label, materiality_label, materiality_score, materiality_reasons, evidence_ids, "
        "change_type, recommended_next_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (signal_id, created_at, signal_type, topic_id, json.dumps(entity, sort_keys=True), summary,
         confidence_label, materiality_label, materiality_score, json.dumps(materiality_reasons),
         json.dumps(evidence_ids), change_type, recommended_next_step),
    )
    return signal_id


def get_material_signal(conn, signal_id):
    row = conn.execute("SELECT * FROM material_signal WHERE signal_id = ?", (signal_id,)).fetchone()
    return _material_signal_row_to_dict(row) if row else None


def get_candidates_for_topic(conn, topic_id):
    rows = conn.execute(
        "SELECT sc.candidate_id, sc.evidence_id, sc.signal_type, sc.summary, sc.confidence, e.captured_at "
        "FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id "
        "WHERE sc.topic_id = ? ORDER BY sc.candidate_id",
        (topic_id,),
    ).fetchall()
    return [dict(r) for r in rows]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_db.py -v`
Expected: PASS (all tests, including the 4 new ones).

- [ ] **Step 5: Commit**

```bash
git add db.py tests/test_db.py
git commit -m "feat: add material_signal table and CRUD to db.py"
```

---

### Task 3: `materiality.py` — classification and signal construction

**Files:**
- Create: `materiality.py`
- Create: `tests/test_materiality.py`

**Interfaces:**
- Consumes: nothing (pure functions, no DB).
- Produces (used by Tasks 4-5): `materiality.confidence_label(avg_confidence: float) -> str`, `materiality.classify_materiality(signal_type: str, trend: str, mentions_this_week: int, avg_confidence: float) -> dict` (returns `{"label", "score", "reasons"}`), `materiality._dominant_signal_type(candidates: list[dict]) -> str`, `materiality.build_material_signal(topic: dict, candidates_this_week: list[dict], trend: str, materiality: dict, today: date) -> dict` (returns a dict with keys matching `db.insert_material_signal`'s kwargs, i.e. `created_at`, `signal_type`, `topic_id`, `entity`, `summary`, `confidence_label`, `materiality_label`, `materiality_score`, `materiality_reasons`, `evidence_ids`, `change_type`, `recommended_next_step`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_materiality.py
from datetime import date

import materiality


def test_confidence_label_buckets_by_threshold():
    assert materiality.confidence_label(0.9) == "high"
    assert materiality.confidence_label(0.7) == "medium"
    assert materiality.confidence_label(0.3) == "low"


def test_classify_materiality_low_for_stable_trend():
    result = materiality.classify_materiality(
        signal_type="new_feature_demand", trend="stable", mentions_this_week=1, avg_confidence=0.9,
    )
    assert result["label"] == "low"


def test_classify_materiality_medium_for_rising_with_moderate_volume():
    result = materiality.classify_materiality(
        signal_type="usability_issue", trend="rising", mentions_this_week=3, avg_confidence=0.7,
    )
    assert result["label"] == "medium"


def test_classify_materiality_high_for_rising_with_high_volume_and_confidence():
    result = materiality.classify_materiality(
        signal_type="complaint_rising", trend="rising", mentions_this_week=6, avg_confidence=0.9,
    )
    assert result["label"] == "high"
    assert result["score"] == 0.8


def test_classify_materiality_high_for_event_driven_signal_type():
    result = materiality.classify_materiality(
        signal_type="pricing_change", trend="new", mentions_this_week=1, avg_confidence=0.95,
    )
    assert result["label"] == "high"


def test_classify_materiality_critical_for_critical_signal_type():
    result = materiality.classify_materiality(
        signal_type="implementation_deadline", trend="new", mentions_this_week=1, avg_confidence=0.9,
    )
    assert result["label"] == "critical"
    assert result["score"] == 0.95


def test_classify_materiality_low_for_event_driven_type_below_confidence_threshold():
    result = materiality.classify_materiality(
        signal_type="pricing_change", trend="new", mentions_this_week=1, avg_confidence=0.5,
    )
    assert result["label"] == "low"


def test_dominant_signal_type_returns_single_type():
    candidates = [
        {"evidence_id": "EV-2026-000001", "signal_type": "new_feature_demand", "summary": "s", "confidence": 0.9},
    ]
    assert materiality._dominant_signal_type(candidates) == "new_feature_demand"


def test_dominant_signal_type_returns_mixed_marker_for_multiple_types():
    candidates = [
        {"evidence_id": "EV-2026-000001", "signal_type": "new_feature_demand", "summary": "s", "confidence": 0.9},
        {"evidence_id": "EV-2026-000002", "signal_type": "usability_issue", "summary": "s2", "confidence": 0.8},
    ]
    assert materiality._dominant_signal_type(candidates) == "mixed_signal_types"


def test_build_material_signal_shape_for_rising_trend():
    topic = {"topic_id": "TOPIC-0001", "name": "Dark mode support"}
    candidates_this_week = [
        {"evidence_id": "EV-2026-000001", "signal_type": "new_feature_demand", "summary": "s", "confidence": 0.9},
        {"evidence_id": "EV-2026-000002", "signal_type": "new_feature_demand", "summary": "s2", "confidence": 0.8},
    ]
    materiality_result = {"label": "high", "score": 0.8, "reasons": ["trend is rising with 6 mentions this week"]}

    signal = materiality.build_material_signal(
        topic, candidates_this_week, trend="rising", materiality=materiality_result, today=date(2026, 8, 23),
    )

    assert signal["created_at"] == "2026-08-23T00:00:00+00:00"
    assert signal["signal_type"] == "new_feature_demand"
    assert signal["topic_id"] == "TOPIC-0001"
    assert signal["entity"] == {"type": "customer_topic", "topic_id": "TOPIC-0001", "topic_name": "Dark mode support"}
    assert signal["confidence_label"] == "high"
    assert signal["materiality_label"] == "high"
    assert signal["materiality_score"] == 0.8
    assert signal["evidence_ids"] == ["EV-2026-000001", "EV-2026-000002"]
    assert signal["change_type"] == "trend_change"
    assert signal["recommended_next_step"] == "strategic_assessment"


def test_build_material_signal_marks_new_event_and_urgent_step_for_critical():
    topic = {"topic_id": "TOPIC-0001", "name": "Dark mode support"}
    candidates_this_week = [
        {"evidence_id": "EV-2026-000001", "signal_type": "pricing_change", "summary": "s", "confidence": 0.95},
    ]
    materiality_result = {"label": "critical", "score": 0.95, "reasons": ["critical-impact signal type"]}

    signal = materiality.build_material_signal(
        topic, candidates_this_week, trend="new", materiality=materiality_result, today=date(2026, 8, 23),
    )

    assert signal["change_type"] == "new_event"
    assert signal["recommended_next_step"] == "urgent_strategic_assessment"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_materiality.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'materiality'`

- [ ] **Step 3: Implement `materiality.py`**

```python
# materiality.py
from datetime import datetime, timezone

EVENT_DRIVEN_HIGH_SIGNAL_TYPES = {
    "product_launch", "feature_launch", "major_feature_improvement", "feature_removal",
    "product_end_of_life", "product_end_of_support", "pricing_change", "packaging_change",
    "free_tier_change", "acquisition", "partnership", "major_customer_win", "api_change",
    "licensing_change", "distribution_change", "new_model_capability", "new_ai_api_capability",
    "open_source_release", "framework_deprecation", "major_platform_feature",
    "ecosystem_standard_change", "platform_policy_change", "marketplace_policy_change",
    "vendor_integration_change", "infrastructure_pricing_change", "cloud_platform_capability_launch",
}

EVENT_DRIVEN_CRITICAL_SIGNAL_TYPES = {
    "regulation_announced", "regulation_approved", "implementation_deadline",
    "compliance_requirement_change",
}

MATERIALITY_LABEL_SCORE = {"low": 0.2, "medium": 0.5, "high": 0.8, "critical": 0.95}

CONFIDENCE_HIGH_THRESHOLD = 0.85
CONFIDENCE_MEDIUM_THRESHOLD = 0.6
EVENT_DRIVEN_CONFIDENCE_THRESHOLD = 0.85
HIGH_TREND_MENTIONS_THRESHOLD = 5
HIGH_TREND_CONFIDENCE_THRESHOLD = 0.85
MEDIUM_TREND_MENTIONS_THRESHOLD = 3


def confidence_label(avg_confidence):
    if avg_confidence >= CONFIDENCE_HIGH_THRESHOLD:
        return "high"
    if avg_confidence >= CONFIDENCE_MEDIUM_THRESHOLD:
        return "medium"
    return "low"


def classify_materiality(signal_type, trend, mentions_this_week, avg_confidence):
    if signal_type in EVENT_DRIVEN_CRITICAL_SIGNAL_TYPES and avg_confidence >= EVENT_DRIVEN_CONFIDENCE_THRESHOLD:
        return {
            "label": "critical", "score": MATERIALITY_LABEL_SCORE["critical"],
            "reasons": [f"{signal_type} is a critical-impact signal type",
                        f"confirmed with confidence {avg_confidence:.2f}"],
        }
    if signal_type in EVENT_DRIVEN_HIGH_SIGNAL_TYPES and avg_confidence >= EVENT_DRIVEN_CONFIDENCE_THRESHOLD:
        return {
            "label": "high", "score": MATERIALITY_LABEL_SCORE["high"],
            "reasons": [f"{signal_type} is a high-impact signal type",
                        f"confirmed with confidence {avg_confidence:.2f}"],
        }
    if (trend == "rising" and mentions_this_week >= HIGH_TREND_MENTIONS_THRESHOLD
            and avg_confidence >= HIGH_TREND_CONFIDENCE_THRESHOLD):
        return {
            "label": "high", "score": MATERIALITY_LABEL_SCORE["high"],
            "reasons": [f"trend is rising with {mentions_this_week} mentions this week",
                        f"average confidence {avg_confidence:.2f} is high"],
        }
    if trend in ("rising", "new") and mentions_this_week >= MEDIUM_TREND_MENTIONS_THRESHOLD:
        return {
            "label": "medium", "score": MATERIALITY_LABEL_SCORE["medium"],
            "reasons": [f"trend is {trend} with {mentions_this_week} mentions this week"],
        }
    return {
        "label": "low", "score": MATERIALITY_LABEL_SCORE["low"],
        "reasons": [f"trend is {trend} with {mentions_this_week} mentions this week"],
    }


def _dominant_signal_type(candidates):
    types = {c["signal_type"] for c in candidates}
    return next(iter(types)) if len(types) == 1 else "mixed_signal_types"


def build_material_signal(topic, candidates_this_week, trend, materiality, today):
    evidence_ids = sorted({c["evidence_id"] for c in candidates_this_week})
    avg_confidence = sum(c["confidence"] for c in candidates_this_week) / len(candidates_this_week)
    signal_type = _dominant_signal_type(candidates_this_week)
    created_at = datetime(today.year, today.month, today.day, tzinfo=timezone.utc).isoformat()

    return {
        "created_at": created_at,
        "signal_type": signal_type,
        "topic_id": topic["topic_id"],
        "entity": {"type": "customer_topic", "topic_id": topic["topic_id"], "topic_name": topic["name"]},
        "summary": f"{topic['name']}: {trend} ({materiality['reasons'][0]})",
        "confidence_label": confidence_label(avg_confidence),
        "materiality_label": materiality["label"],
        "materiality_score": materiality["score"],
        "materiality_reasons": materiality["reasons"],
        "evidence_ids": evidence_ids,
        "change_type": "new_event" if trend == "new" else "trend_change",
        "recommended_next_step": (
            "urgent_strategic_assessment" if materiality["label"] == "critical" else "strategic_assessment"
        ),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_materiality.py -v`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add materiality.py tests/test_materiality.py
git commit -m "feat: add materiality classification and material_signal construction"
```

---

### Task 4: `materiality.py` — event emission

**Files:**
- Modify: `materiality.py`
- Modify: `tests/test_materiality.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (used by Task 5): `materiality.EVENT_NAME`, `materiality.DEFAULT_EVENTS_PATH`, `materiality.emit_event(material_signal: dict, events_path=DEFAULT_EVENTS_PATH) -> dict` — `material_signal` here must already include a `signal_id` key (added by the caller after `db.insert_material_signal` returns it).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_materiality.py`:

```python
import json


def test_emit_event_appends_jsonl_envelope(tmp_path):
    events_path = tmp_path / "events.jsonl"
    material_signal = {
        "signal_id": "SIG-2026-0001", "created_at": "2026-08-23T00:00:00+00:00",
        "signal_type": "new_feature_demand", "summary": "Dark mode support: rising",
        "confidence_label": "high", "materiality_label": "high",
        "evidence_ids": ["EV-2026-000001", "EV-2026-000002"],
    }

    envelope = materiality.emit_event(material_signal, events_path=str(events_path))

    assert envelope == {
        "event": "product_intelligence.signal.material",
        "event_version": "1.0",
        "timestamp": "2026-08-23T00:00:00+00:00",
        "signal": {
            "signal_id": "SIG-2026-0001", "signal_type": "new_feature_demand",
            "summary": "Dark mode support: rising", "confidence": "high", "materiality": "high",
            "evidence_ids": ["EV-2026-000001", "EV-2026-000002"],
        },
    }

    with open(events_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert json.loads(lines[0]) == envelope


def test_emit_event_appends_without_truncating_existing_events(tmp_path):
    events_path = tmp_path / "events.jsonl"
    signal_one = {
        "signal_id": "SIG-2026-0001", "created_at": "2026-08-23T00:00:00+00:00",
        "signal_type": "new_feature_demand", "summary": "first", "confidence_label": "high",
        "materiality_label": "high", "evidence_ids": ["EV-2026-000001"],
    }
    signal_two = {
        "signal_id": "SIG-2026-0002", "created_at": "2026-08-30T00:00:00+00:00",
        "signal_type": "new_feature_demand", "summary": "second", "confidence_label": "high",
        "materiality_label": "high", "evidence_ids": ["EV-2026-000002"],
    }

    materiality.emit_event(signal_one, events_path=str(events_path))
    materiality.emit_event(signal_two, events_path=str(events_path))

    with open(events_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 2
    assert json.loads(lines[1])["signal"]["signal_id"] == "SIG-2026-0002"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_materiality.py -v`
Expected: FAIL with `AttributeError: module 'materiality' has no attribute 'emit_event'`

- [ ] **Step 3: Implement the changes in `materiality.py`**

Add `import json` and `import os` to the top of `materiality.py`, and add this to the bottom:

```python
EVENT_NAME = "product_intelligence.signal.material"
EVENT_VERSION = "1.0"
DEFAULT_EVENTS_PATH = "data/events.jsonl"


def emit_event(material_signal, events_path=DEFAULT_EVENTS_PATH):
    envelope = {
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
    os.makedirs(os.path.dirname(events_path) or ".", exist_ok=True)
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(envelope) + "\n")
    return envelope
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_materiality.py -v`
Expected: PASS (13 tests).

- [ ] **Step 5: Commit**

```bash
git add materiality.py tests/test_materiality.py
git commit -m "feat: emit product_intelligence.signal.material events as JSONL"
```

---

### Task 5: `materiality.py` — `run()` orchestration

**Files:**
- Modify: `materiality.py`
- Modify: `tests/test_materiality.py`

**Interfaces:**
- Consumes: `db.connect`, `db.init_db`, `db.get_canonical_topics`, `db.get_topic_weekly_mentions`, `db.get_candidates_for_topic`, `db.insert_material_signal` (Task 2); `report.compute_trends` (existing); `weekutil.iso_week_string` (existing); `classify_materiality`, `_dominant_signal_type`, `build_material_signal`, `emit_event` (Tasks 3-4).
- Produces (used by Task 6): `materiality.run(db_path="data/pi_agent.db", trend_window_weeks=8, today=None, events_path=DEFAULT_EVENTS_PATH) -> None`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_materiality.py`:

```python
from datetime import date

import db


def test_run_creates_material_signal_and_emits_event_for_high_materiality_topic(tmp_path):
    db_path = tmp_path / "pi_agent.db"
    events_path = tmp_path / "events.jsonl"
    conn = db.connect(str(db_path))
    db.init_db(conn)

    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=1)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W31", amount=1)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W32", amount=1)
    for i in range(6):
        evidence_id = db.insert_evidence(
            conn, source_type="reddit_post", source_name="sub", source_url=f"/p{i}",
            captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
            title="t", content="c", metadata={},
        )
        db.insert_signal_candidate(
            conn, evidence_id=evidence_id, signal_type="new_feature_demand",
            summary="s", confidence=0.9, topic_id=topic_id,
        )
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W33", amount=6)
    conn.commit()
    conn.close()

    materiality.run(db_path=str(db_path), today=date(2026, 8, 15), events_path=str(events_path))

    conn = db.connect(str(db_path))
    signals = conn.execute("SELECT signal_id FROM material_signal").fetchall()
    conn.close()
    assert len(signals) == 1

    with open(events_path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    assert len(lines) == 1


def test_run_does_not_create_signal_for_low_materiality_topic(tmp_path):
    db_path = tmp_path / "pi_agent.db"
    events_path = tmp_path / "events.jsonl"
    conn = db.connect(str(db_path))
    db.init_db(conn)

    topic_id = db.insert_canonical_topic(
        conn, slug="minor-topic", name="Minor topic", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W32",
    )
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=5)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W31", amount=5)
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W32", amount=5)
    evidence_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="/p1",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="t", content="c", metadata={},
    )
    db.insert_signal_candidate(
        conn, evidence_id=evidence_id, signal_type="new_feature_demand",
        summary="s", confidence=0.9, topic_id=topic_id,
    )
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W33", amount=1)
    conn.commit()
    conn.close()

    materiality.run(db_path=str(db_path), today=date(2026, 8, 15), events_path=str(events_path))

    conn = db.connect(str(db_path))
    signals = conn.execute("SELECT signal_id FROM material_signal").fetchall()
    conn.close()
    assert signals == []
    assert not events_path.exists()


def test_run_skips_topics_with_no_new_evidence_this_week(tmp_path):
    db_path = tmp_path / "pi_agent.db"
    events_path = tmp_path / "events.jsonl"
    conn = db.connect(str(db_path))
    db.init_db(conn)
    topic_id = db.insert_canonical_topic(
        conn, slug="idle-topic", name="Idle topic", description="d",
        aliases=[], first_seen="2026-W30", last_seen="2026-W30",
    )
    db.increment_topic_weekly_mentions(conn, topic_id, "2026-W30", amount=1)
    conn.commit()
    conn.close()

    materiality.run(db_path=str(db_path), today=date(2026, 8, 15), events_path=str(events_path))

    conn = db.connect(str(db_path))
    signals = conn.execute("SELECT signal_id FROM material_signal").fetchall()
    conn.close()
    assert signals == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_materiality.py -v`
Expected: FAIL with `AttributeError: module 'materiality' has no attribute 'run'`

- [ ] **Step 3: Implement the changes in `materiality.py`**

Add `from datetime import date` (alongside the existing `datetime, timezone` import), and add `import db`, `import report`, `from weekutil import iso_week_string` to the top of `materiality.py`. Add this to the bottom:

```python
def _week_of_iso_timestamp(iso_timestamp):
    return iso_week_string(datetime.fromisoformat(iso_timestamp).date())


def run(db_path="data/pi_agent.db", trend_window_weeks=8, today=None, events_path=DEFAULT_EVENTS_PATH):
    run_date = today or date.today()
    week = iso_week_string(run_date)
    conn = db.connect(db_path)
    db.init_db(conn)

    topics = db.get_canonical_topics(conn)
    for topic in topics:
        topic["weekly_mentions"] = db.get_topic_weekly_mentions(conn, topic["topic_id"])
    trend_by_topic_id = {row["id"]: row for row in report.compute_trends(topics, week, trend_window_weeks)}

    created_signals = []
    try:
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

            material_signal = build_material_signal(
                topic, candidates_this_week, trend_row["trend"], materiality_result, run_date,
            )
            signal_id = db.insert_material_signal(conn, **material_signal)
            created_signals.append({**material_signal, "signal_id": signal_id})
    except Exception:
        conn.rollback()
        conn.close()
        raise

    conn.commit()
    conn.close()

    for material_signal in created_signals:
        emit_event(material_signal, events_path=events_path)


if __name__ == "__main__":
    run()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_materiality.py -v`
Expected: PASS (16 tests).

- [ ] **Step 5: Commit**

```bash
git add materiality.py tests/test_materiality.py
git commit -m "feat: add materiality.run() orchestration against the SQLite pipeline"
```

---

### Task 6: Wire `materiality` into `run_weekly.py`

**Files:**
- Modify: `run_weekly.py`
- Modify: `tests/test_run_weekly.py`

**Interfaces:**
- Consumes: `materiality.run` (Task 5).
- Produces: `run_weekly.STAGES` now includes `"materiality"`; `run_weekly.run_all` and `run_weekly.main` call `materiality.run(trend_window_weeks=config["trend_window_weeks"], today=today)` after `report.run(...)`.

- [ ] **Step 1: Write the failing tests**

Replace the three existing tests in `tests/test_run_weekly.py` with:

```python
import sys

import run_weekly


def test_run_all_calls_every_stage_in_order(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\nfetch_limit_per_subreddit: 25\ntrend_window_weeks: 4\n")

    calls = []
    monkeypatch.setattr(run_weekly.fetch, "run", lambda *a, **kw: calls.append(("fetch", a, kw)))
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: calls.append(("extract", a, kw)))
    monkeypatch.setattr(run_weekly.match, "run", lambda *a, **kw: calls.append(("match", a, kw)))
    monkeypatch.setattr(run_weekly.report, "run", lambda *a, **kw: calls.append(("report", a, kw)))
    monkeypatch.setattr(run_weekly.materiality, "run", lambda *a, **kw: calls.append(("materiality", a, kw)))

    run_weekly.run_all(config_path=str(config_path))

    assert [c[0] for c in calls] == ["fetch", "extract", "match", "report", "materiality"]
    fetch_call = calls[0]
    assert fetch_call[1] == (["sub1"], 25)
    report_call = calls[3]
    assert report_call[2]["trend_window_weeks"] == 4
    materiality_call = calls[4]
    assert materiality_call[2]["trend_window_weeks"] == 4


def test_main_dash_stage_fetch_runs_only_fetch(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\n")

    calls = []
    monkeypatch.setattr(run_weekly.fetch, "run", lambda *a, **kw: calls.append("fetch"))
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: calls.append("extract"))
    monkeypatch.setattr(run_weekly.match, "run", lambda *a, **kw: calls.append("match"))
    monkeypatch.setattr(run_weekly.report, "run", lambda *a, **kw: calls.append("report"))
    monkeypatch.setattr(run_weekly.materiality, "run", lambda *a, **kw: calls.append("materiality"))

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "fetch", "--config", str(config_path)])
    run_weekly.main()

    assert calls == ["fetch"]


def test_main_dash_stage_materiality_runs_only_materiality(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\n")

    calls = []
    monkeypatch.setattr(run_weekly.fetch, "run", lambda *a, **kw: calls.append("fetch"))
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: calls.append("extract"))
    monkeypatch.setattr(run_weekly.match, "run", lambda *a, **kw: calls.append("match"))
    monkeypatch.setattr(run_weekly.report, "run", lambda *a, **kw: calls.append("report"))
    monkeypatch.setattr(run_weekly.materiality, "run", lambda *a, **kw: calls.append("materiality"))

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "materiality", "--config", str(config_path)])
    run_weekly.main()

    assert calls == ["materiality"]


def test_main_defaults_to_all_stages(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\n")

    calls = []
    monkeypatch.setattr(run_weekly.fetch, "run", lambda *a, **kw: calls.append("fetch"))
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: calls.append("extract"))
    monkeypatch.setattr(run_weekly.match, "run", lambda *a, **kw: calls.append("match"))
    monkeypatch.setattr(run_weekly.report, "run", lambda *a, **kw: calls.append("report"))
    monkeypatch.setattr(run_weekly.materiality, "run", lambda *a, **kw: calls.append("materiality"))

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--config", str(config_path)])
    run_weekly.main()

    assert calls == ["fetch", "extract", "match", "report", "materiality"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_run_weekly.py -v`
Expected: FAIL — `run_weekly` has no attribute `materiality`, and `"materiality"` is not a valid `--stage` choice.

- [ ] **Step 3: Implement the changes in `run_weekly.py`**

```python
# run_weekly.py
import argparse

import extract
import fetch
import match
import materiality
import report
from config import load_config

STAGES = ["fetch", "extract", "match", "report", "materiality"]


def run_all(config_path="config.yaml", today=None):
    config = load_config(config_path)
    fetch.run(config["subreddits"], config["fetch_limit_per_subreddit"], today=today)
    extract.run(today=today)
    match.run(today=today)
    report.run(trend_window_weeks=config["trend_window_weeks"], today=today)
    materiality.run(trend_window_weeks=config["trend_window_weeks"], today=today)


def main():
    parser = argparse.ArgumentParser(description="Reddit signal pipeline")
    parser.add_argument("--stage", choices=STAGES + ["all"], default="all")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    if args.stage == "all":
        run_all(args.config)
        return

    config = load_config(args.config)
    if args.stage == "fetch":
        fetch.run(config["subreddits"], config["fetch_limit_per_subreddit"])
    elif args.stage == "extract":
        extract.run()
    elif args.stage == "match":
        match.run()
    elif args.stage == "report":
        report.run(trend_window_weeks=config["trend_window_weeks"])
    elif args.stage == "materiality":
        materiality.run(trend_window_weeks=config["trend_window_weeks"])


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_run_weekly.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add run_weekly.py tests/test_run_weekly.py
git commit -m "feat: wire materiality stage into run_weekly.py"
```

---

### Task 7: `query.py` — `signal` command

**Files:**
- Modify: `query.py`
- Modify: `tests/test_query.py`

**Interfaces:**
- Consumes: `db.get_material_signal` (Task 2).
- Produces: `query.signal_command(conn, signal_id) -> None` (prints JSON or a not-found message); `python query.py signal <signal_id>` CLI subcommand.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_query.py`:

```python
def seed_material_signal(db_path):
    conn = db.connect(db_path)
    evidence_id = db.insert_evidence(
        conn, source_type="reddit_post", source_name="sub", source_url="https://reddit.com/x",
        captured_at="2026-08-15T00:00:00+00:00", published_at="2026-08-14T00:00:00+00:00",
        title="t", content="c", metadata={},
    )
    topic_id = db.insert_canonical_topic(
        conn, slug="dark-mode-support", name="Dark mode support", description="d",
        aliases=[], first_seen="2026-W33", last_seen="2026-W33",
    )
    signal_id = db.insert_material_signal(
        conn, created_at="2026-08-15T00:00:00+00:00", signal_type="new_feature_demand", topic_id=topic_id,
        entity={"type": "customer_topic", "topic_id": topic_id, "topic_name": "Dark mode support"},
        summary="Dark mode support: rising", confidence_label="high", materiality_label="high",
        materiality_score=0.8, materiality_reasons=["trend is rising"], evidence_ids=[evidence_id],
        change_type="trend_change", recommended_next_step="strategic_assessment",
    )
    conn.commit()
    conn.close()
    return signal_id


def test_signal_command_prints_material_signal(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    signal_id = seed_material_signal(db_path)
    conn = db.connect(db_path)

    query.signal_command(conn, signal_id)

    conn.close()
    output = json.loads(capsys.readouterr().out)
    assert output["signal_id"] == signal_id
    assert output["materiality_label"] == "high"


def test_signal_command_reports_unknown_id(tmp_path, capsys):
    db_path = seed_db(tmp_path)
    conn = db.connect(db_path)

    query.signal_command(conn, "SIG-2026-9999")

    conn.close()
    assert "no material signal found" in capsys.readouterr().out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_query.py -v`
Expected: FAIL with `AttributeError: module 'query' has no attribute 'signal_command'`

- [ ] **Step 3: Implement the changes in `query.py`**

```python
# query.py
def signal_command(conn, signal_id):
    signal = db.get_material_signal(conn, signal_id)
    if signal is None:
        print(f"no material signal found with id {signal_id!r}")
        return
    print(json.dumps(signal, indent=2))
```

Add the subparser in `main()`, alongside `topic_parser`/`search_parser`:

```python
    signal_parser = subparsers.add_parser("signal")
    signal_parser.add_argument("signal_id")
```

And the dispatch branch:

```python
    elif args.command == "signal":
        signal_command(conn, args.signal_id)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_query.py -v`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add query.py tests/test_query.py
git commit -m "feat: add query.py signal command for material_signal inspection"
```

---

### Task 8: End-to-end test — full vertical slice to a material signal

**Files:**
- Modify: `tests/test_end_to_end.py`

**Interfaces:**
- Consumes: `materiality.run` (Task 5), plus the existing `fetch.run`/`extract.run`/`match.run`/`report.run`.

- [ ] **Step 1: Write the failing test**

Add `import materiality` to the top of `tests/test_end_to_end.py`, and add this test:

```python
def test_pipeline_emits_material_signal_when_topic_crosses_high_threshold(tmp_path):
    state_path = tmp_path / "state.json"
    db_path = tmp_path / "pi_agent.db"
    events_path = tmp_path / "events.jsonl"

    week1_client = FakeRedditClient({
        "yourproductname": FakeSubreddit([
            FakeSubmission(
                fullname="t3_p1", id="p1", title="Please add dark mode",
                selftext="Would love a dark theme", permalink="/r/yourproductname/comments/p1",
                score=20, num_comments=4, created_utc=1700000000.0,
            ),
        ]),
    })
    fetch.run(
        subreddits=["yourproductname"], fetch_limit_per_subreddit=10,
        state_path=str(state_path), db_path=str(db_path), today=date(2026, 8, 15),
        reddit_client=week1_client,
    )
    extract.run(db_path=str(db_path), today=date(2026, 8, 15), client=FakeAnthropicClient([
        json.dumps({"signal_type": "new_feature_demand", "summary": "User wants dark theme", "confidence": 0.9}),
    ]))
    match.run(db_path=str(db_path), today=date(2026, 8, 15), client=FakeAnthropicClient([
        json.dumps([{
            "index": 0, "matched_topic_id": None,
            "new_topic": {"name": "Dark mode support", "slug": "dark-mode-support",
                          "description": "Users requesting a dark theme option"},
        }]),
    ]))
    materiality.run(db_path=str(db_path), today=date(2026, 8, 15), events_path=str(events_path))

    conn = db.connect(str(db_path))
    assert conn.execute("SELECT COUNT(*) FROM material_signal").fetchone()[0] == 0
    conn.close()
    assert not events_path.exists()

    week2_posts = [
        FakeSubmission(
            fullname=f"t3_p{i}", id=f"p{i}", title="More dark mode requests",
            selftext="Still no dark theme", permalink=f"/r/yourproductname/comments/p{i}",
            score=5, num_comments=1, created_utc=1700500000.0,
        )
        for i in range(2, 8)
    ]
    week2_client = FakeRedditClient({"yourproductname": FakeSubreddit(week2_posts)})
    fetch.run(
        subreddits=["yourproductname"], fetch_limit_per_subreddit=10,
        state_path=str(state_path), db_path=str(db_path), today=date(2026, 8, 22),
        reddit_client=week2_client,
    )
    extract.run(db_path=str(db_path), today=date(2026, 8, 22), client=FakeAnthropicClient([
        json.dumps({"signal_type": "new_feature_demand", "summary": "User wants dark theme", "confidence": 0.9})
        for _ in range(6)
    ]))
    match.run(db_path=str(db_path), today=date(2026, 8, 22), client=FakeAnthropicClient([
        json.dumps([{"index": i, "matched_topic_id": "TOPIC-0001", "new_topic": None} for i in range(6)]),
    ]))
    materiality.run(db_path=str(db_path), today=date(2026, 8, 22), events_path=str(events_path))

    conn = db.connect(str(db_path))
    signals = conn.execute("SELECT signal_id, materiality_label FROM material_signal").fetchall()
    conn.close()
    assert len(signals) == 1
    assert signals[0]["materiality_label"] == "high"

    with open(events_path, "r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f]
    assert len(events) == 1
    assert events[0]["event"] == "product_intelligence.signal.material"
    assert events[0]["signal"]["materiality"] == "high"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_end_to_end.py -v`
Expected: FAIL — `materiality` isn't imported yet / doesn't produce the expected rows before Tasks 2-5 land. (If run after Tasks 1-7 are already committed, this should mostly pass on the first try; treat any failure as a real integration bug per Step 3.)

- [ ] **Step 3: Fix any integration issues found**

If any stage's real behavior doesn't match what Tasks 2-5 documented, fix the module — not the test — unless the test itself has a mistake.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_end_to_end.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest -v`
Expected: all tests pass (`test_config.py`, `test_credentials.py`, `test_db.py`, `test_end_to_end.py`, `test_extract.py`, `test_fetch.py`, `test_match.py`, `test_materiality.py`, `test_query.py`, `test_report.py`, `test_run_weekly.py`, `test_set_credentials.py`, `test_state.py`, `test_weekutil.py`).

- [ ] **Step 6: Commit**

```bash
git add tests/test_end_to_end.py
git commit -m "test: add end-to-end scenario proving a topic reaches material-signal emission"
```

---

### Task 9: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing new — documentation only.

- [ ] **Step 1: Update the "Architecture" section**

Replace the pipeline diagram and stage list with:

```markdown
```text
run_weekly.py
  └─> fetch.py        → evidence table
  └─> extract.py      → signal_candidate table
  └─> match.py        → canonical_topic + topic_weekly_mentions tables
  └─> report.py       → data/reports/<week>.json, data/reports/<week>.csv
  └─> materiality.py  → material_signal table, data/events.jsonl
```

- **fetch.py** — pulls new posts per subreddit via PRAW, storing each as an immutable
  `evidence` row.
- **extract.py** — classifies each not-yet-processed evidence row into a `signal_type`
  (from the product intelligence signal taxonomy) plus a confidence score, storing it as
  a `signal_candidate`.
- **match.py** — matches new candidates against the `canonical_topic` registry (or
  creates a new topic), incrementing per-week mention counts.
- **report.py** — computes top topics by mention count and week-over-week trend
  (new / rising / stable / falling) from the database.
- **materiality.py** — classifies each topic's this-week materiality (low / medium /
  high / critical) from its trend, volume, confidence, and signal type; `high`/`critical`
  topics get a `material_signal` row and a `product_intelligence.signal.material` event
  appended to `data/events.jsonl`.

Use `python query.py topic <slug>`, `python query.py search <keyword>`, or
`python query.py signal <signal_id>` for ad hoc inspection of the DB during development.
```

- [ ] **Step 2: Add a "Vision & roadmap" pointer**

Add this section after "Architecture":

```markdown
## Vision & roadmap

This repo implements a slice of a larger product-intelligence-agent vision — see
[docs/product-intelligence-agent-vision.md](docs/product-intelligence-agent-vision.md).
Currently implemented: Reddit ingestion, customer-market signal extraction, canonical
topic matching, deterministic trend detection, and materiality-gated event emission.
Not yet implemented: additional Phase-1 sources (GitHub Issues, competitor changelogs,
customer-interview summaries), the formal Integration API (section 11), scheduled/
event-driven launch modes, the natural-language query interface, and the evaluation
harness (section 14).
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: document the materiality stage and link the product-intelligence vision doc"
```

---

## Self-Review

**Spec coverage:**
- Retiring dead one-time-migration code (`registry.py`/`migrate_to_sqlite.py`) — Task 1. ✓
- `material_signal` schema + CRUD (vision doc §8.1 object shape) — Task 2. ✓
- Materiality classification, both event-driven (§5.2-§5.5 signal types → high/critical) and trend-driven (§5.1 signal types → low/medium/high) paths (vision doc §9) — Task 3. ✓
- Event contract `product_intelligence.signal.material` (vision doc §10) — Task 4. ✓
- End-to-end weekly orchestration tying trend + materiality + signal + event together, transactional per Global Constraints — Task 5. ✓
- Pipeline wiring (`run_weekly.py`) — Task 6. ✓
- Ad hoc inspection (`query.py signal`) — Task 7. ✓
- Full vertical-slice proof (evidence → candidate → topic/trend → material signal → event) matching the vision doc's §15 demo scenario shape (minus the multi-source verification step, which needs a second source not yet built) — Task 8. ✓
- Documentation of what's implemented vs. still open, so the next audit doesn't have to re-derive it — Task 9. ✓

**Out of scope, confirmed absent from this plan (deferred to later sub-projects, per the audit):** GitHub Issues / competitor-changelog / customer-interview sources, the formal `get_signal`/`get_evidence`/`search_related_signals`/`get_topic_trend`/`search_feedback` API, scheduled/event-driven launch modes, the natural-language query interface, the evaluation harness, `sharply_rising`/`resurfacing`/`dormant` trend states, cross-run signal deduplication, and competitor/company `entity` extraction.

**Placeholder scan:** no `TBD`/`TODO`/"implement later" markers; every step includes concrete code or an exact command.

**Type consistency check:** `build_material_signal`'s return dict keys (`created_at`, `signal_type`, `topic_id`, `entity`, `summary`, `confidence_label`, `materiality_label`, `materiality_score`, `materiality_reasons`, `evidence_ids`, `change_type`, `recommended_next_step`) match `db.insert_material_signal`'s keyword parameters exactly, so `db.insert_material_signal(conn, **material_signal)` in Task 5 works without adaptation. `classify_materiality`'s return shape (`label`/`score`/`reasons`) is used identically in `build_material_signal` (Task 3) and `materiality.run` (Task 5). `db.get_candidates_for_topic`'s dict keys (`candidate_id`, `evidence_id`, `signal_type`, `summary`, `confidence`, `captured_at`) match what `_dominant_signal_type` and `materiality.run`'s week-filtering both expect.
