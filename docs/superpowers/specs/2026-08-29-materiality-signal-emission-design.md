# Materiality & Signal Emission — Design

## Purpose

The product intelligence vision (see the "Product Intelligence Agent" north-star doc,
saved at [docs/product-intelligence-agent-vision.md](../../product-intelligence-agent-vision.md))
describes a pipeline that goes all the way from raw evidence to a `material signal`
event handed off to a downstream Strategic Signals Agent. Today's pipeline
(`fetch.py` → `extract.py` → `match.py` → `report.py`) stops at deterministic
trend computation: it can tell you a topic is `rising`, but it never decides
whether that's important enough to act on, and it never tells anyone.

This sub-project adds the two missing links: **materiality classification**
(vision doc §8/§9) and **event emission** (vision doc §10). It builds directly
on the existing `evidence` / `signal_candidate` / `canonical_topic` /
`topic_weekly_mentions` tables — no new source, no new external dependency.

## Non-goals

- No LLM call for materiality — the trend engine (`report.py`) is already
  deterministic and this reuses that decision; adding AI judgment here is a
  separate, later enhancement.
- No real webhook/network delivery for the event contract — emission means
  appending a JSON line to a local `data/events.jsonl` file. There is no
  Strategic Signals Agent service to call yet.
- No `entity` extraction beyond the current Reddit-topic shape
  (`{"type": "customer_topic", "topic_id": ..., "topic_name": ...}`). The
  vision doc's competitor/company entity examples require a competitor source
  that doesn't exist in this repo yet.
- No `effective_date` field — that's meaningful for competitor
  end-of-support/pricing announcements, not for aggregated customer complaints.
- No cross-run deduplication beyond what naturally falls out of scoping to
  "this week's new candidates" (see Open items).
- No formal `get_signal`/`search_related_signals` API (vision doc §11) —
  `query.py` gets one more ad hoc command (`signal <signal_id>`) for manual
  inspection only, same "not a stable contract" caveat as its existing commands.
- Does not touch `registry.py` / `migrate_to_sqlite.py` functionality — those
  are deleted outright in this plan because they are dead one-time-migration
  code, not because of anything to do with materiality.

## Architecture

A fifth pipeline stage, `materiality.py`, runs after `report.py` (same
`run_weekly.py` entrypoint, same "plain module with a `run()`" shape as every
other stage):

```text
run_weekly.py
  └─> fetch.py       → evidence table
  └─> extract.py     → signal_candidate table
  └─> match.py       → canonical_topic + topic_weekly_mentions tables
  └─> report.py      → data/reports/<week>.json, data/reports/<week>.csv
  └─> materiality.py → material_signal table, data/events.jsonl
```

### materiality.py

For each canonical topic, on each run:

1. Compute this week's trend the same way `report.py` does (reuses
   `report.compute_trends`).
2. Gather the `signal_candidate` rows linked to that topic whose evidence was
   captured in the current ISO week (`db.get_candidates_for_topic`, filtered
   by week).
3. If there are no new candidates this week for that topic, skip it — nothing
   changed, nothing to (re-)assess.
4. Classify materiality (`classify_materiality`) using two independent rule
   paths:
   - **Event-driven**: some signal types (vision doc §5.2 competitive, §5.3
     technology, §5.5 ecosystem → `high`; §5.4 regulatory → `critical`) are
     inherently material on a single high-confidence occurrence, regardless of
     trend. None of today's Reddit-only §5.1 customer-market signal types
     appear in these sets, so this path is exercised by direct unit tests
     today and becomes reachable once a second, non-Reddit source is added.
   - **Trend-driven**: for the ordinary customer-market signal types, a
     `rising` trend with enough volume and average confidence is `high`; a
     smaller `rising`/`new` bump is `medium`; everything else is `low`.
5. `low`/`medium` topics stop here — the vision doc's own examples say `low`
   is "store only" and `medium` is "update topic" (already true: `match.py`
   already updated the topic this run). No new row, no event.
6. `high`/`critical` topics get a `material_signal` row (vision doc §8 shape)
   and, once the whole run's DB transaction has committed, one JSON line
   appended to `data/events.jsonl` in the vision doc §10 envelope shape.

### db.py

One new table, `material_signal`, plus:
`insert_material_signal`, `get_material_signal`, `get_candidates_for_topic`.

### query.py

One new subcommand, `signal <signal_id>`, printing a `material_signal` row —
same manual-inspection role as the existing `topic`/`search` commands.

## Data model

```sql
CREATE TABLE material_signal (
  signal_id TEXT PRIMARY KEY,           -- SIG-<year>-<4-digit sequence>
  created_at TEXT NOT NULL,
  signal_type TEXT NOT NULL,            -- a signal_type, or "mixed_signal_types"
  topic_id TEXT REFERENCES canonical_topic(topic_id),
  entity TEXT NOT NULL,                 -- JSON
  summary TEXT NOT NULL,
  confidence_label TEXT NOT NULL,       -- low | medium | high
  materiality_label TEXT NOT NULL,      -- low | medium | high | critical
  materiality_score REAL NOT NULL,      -- representative anchor per label, not a fitted formula
  materiality_reasons TEXT NOT NULL,    -- JSON list[str]
  evidence_ids TEXT NOT NULL,           -- JSON list[str]
  change_type TEXT NOT NULL,            -- new_event | trend_change
  recommended_next_step TEXT NOT NULL   -- strategic_assessment | urgent_strategic_assessment
);
```

Event envelope appended to `data/events.jsonl` (one JSON object per line):

```json
{
  "event": "product_intelligence.signal.material",
  "event_version": "1.0",
  "timestamp": "2026-08-23T00:00:00+00:00",
  "signal": {
    "signal_id": "SIG-2026-0001",
    "signal_type": "new_feature_demand",
    "summary": "...",
    "confidence": "high",
    "materiality": "high",
    "evidence_ids": ["EV-2026-000001", "EV-2026-000002"]
  }
}
```

## Error handling

Same pattern as every other stage: one transaction per `materiality.run()`
invocation, `conn.rollback()` and re-raise on any unhandled exception. Event
emission happens only after the transaction has committed, so a crash never
emits an event for data that didn't actually get persisted, and never leaves
a `material_signal` row without a corresponding event (or vice versa).

## Testing

Pure-function unit tests for `classify_materiality`/`confidence_label`/
`build_material_signal`/`emit_event` (no DB needed), `db.py` CRUD tests
following the existing `test_db.py` pattern, an orchestration test for
`materiality.run()` against a real (temp-file) SQLite DB, and an end-to-end
test extending `test_end_to_end.py` across two simulated weekly runs so a
topic actually crosses the `high` threshold and an event is observed on disk.

## Open items for implementation planning

- Re-triggering: a topic that stays `high` for several consecutive weeks
  (still getting enough new high-confidence mentions each week) will emit a
  new `material_signal` + event every week. This is a deliberate choice for
  this first cut — it matches "ongoing important issue" rather than
  "one-shot alert" — but is worth revisiting once there's a real downstream
  consumer to gauge event volume against.
- `registry.py` / `migrate_to_sqlite.py` cleanup is folded into this plan's
  first task purely to keep the tree tidy before adding new modules; it has
  no functional relationship to materiality.
