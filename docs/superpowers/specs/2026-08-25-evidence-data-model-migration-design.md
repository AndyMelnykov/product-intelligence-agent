# Evidence Data Model Migration — Design

## Purpose

Migrate the existing Reddit signal pipeline (`fetch.py` → `extract.py` → `match.py` →
`report.py`) from ad hoc JSON files (`data/state.json`, `data/registry.json`,
`data/raw/`, `data/extracted/`) onto the evidence/signal-candidate/canonical-topic data
model described in
[04-product-intelligence-agent.md](../../../04-product-intelligence-agent.md) sections 4
and 6, backed by SQLite instead of flat files.

This is sub-project 1 of 5 in the path toward the full Product Intelligence Agent
described in that spec. It establishes the foundational data model that every later
sub-project (materiality engine, event contract/API, additional source connectors,
launch modes) builds on. Reddit remains the only source; no new sources are added here.

## Non-goals

- No materiality scoring or material signal objects (target spec sections 8-9) — next
  sub-project.
- No `product_intelligence.signal.material` event emission (section 10) — later
  sub-project.
- No formal `get_signal`/`get_evidence`/`search_related_signals`/`get_topic_trend`/
  `search_feedback` API (section 11) — later sub-project. `query.py` in this sub-project
  is a throwaway ad hoc CLI, not that API, and should not be treated as a stable
  interface other components depend on.
- No GitHub Issues, competitor changelog, or manual customer-interview sources (section
  3) — later sub-project.
- No scheduler, event-driven mode, or natural-language query interface (sections 12-13)
  — later sub-project.
- No evaluation harness (section 14) — later sub-project.

## Architecture

Same four-stage shape as today, chained by the same `run_weekly.py` entrypoint. Each
stage now reads/writes SQLite tables instead of bespoke JSON files. A new `query.py`
provides ad hoc read access to the DB.

```text
run_weekly.py
  └─> fetch.py    → evidence table (SQLite)
  └─> extract.py  → signal_candidate table (SQLite)
  └─> match.py    → canonical_topic + topic_weekly_mentions tables (SQLite)
  └─> report.py   → data/reports/<week>.json, data/reports/<week>.csv (unchanged output)
  └─> query.py    → ad hoc read CLI against the DB (new)
```

`data/state.json` (last-seen Reddit post fullname per subreddit) remains a small
standalone file — it is fetch-cursor bookkeeping, not evidence, and doesn't belong in
the evidence model.

### fetch.py

- Unchanged Reddit/PRAW fetching logic and field selection (`id`, `title`, `selftext`,
  `permalink`, `score`, `num_comments`, `created_utc`; no author/username; skip
  deleted/removed).
- Writes one `evidence` row per new post instead of
  `data/raw/<week>/<subreddit>.json`.
- `source_type='reddit_post'`, `source_name=<subreddit>`, `source_url=<permalink>`,
  `captured_at=<run timestamp>`, `published_at=<created_utc, ISO>`,
  `title=<post title>`, `content=<selftext>`,
  `metadata=<JSON: {"score", "num_comments"}>`.
- `evidence_id` format: `EV-<year>-<zero-padded sequence>`.
- `data/state.json` update timing unchanged: only after a fully successful run across
  all configured subreddits.

### extract.py

- Claude prompt extended from the old 5-value category enum
  (`feature_request`/`complaint`/`praise`/`question`/`competitor_comparison`) to the
  full section-5 `signal_type` taxonomy (customer-market signals only, since Reddit is
  the only source: `complaint_rising`, `complaint_falling`, `new_feature_demand`,
  `new_use_case`, `usability_issue`, `reliability_issue`, `pricing_complaint`,
  `switching_intent`, `competitor_mention_rising`, `customer_migration_intent`,
  `churn_related_issue`, `positive_adoption_pattern`).
- Also extracts a `confidence` score (0.0-1.0) per the section-4.2 candidate shape.
- One post's extraction failure is still logged and skipped, not fatal to the batch
  (unchanged behavior).
- Writes one `signal_candidate` row per successfully extracted post, linked to its
  `evidence_id`. `candidate_id` format: `SC-<year>-<zero-padded sequence>`.
- `topic_id` on the candidate is NULL until `match.py` assigns it.

### match.py

- Loads all `canonical_topic` rows (was: `registry.json`'s `topics` list).
- Batches new `signal_candidate` rows (this run) + existing canonical topics into a
  single Claude matching prompt per run, same semantics as today (semantic match vs.
  new topic).
- On match: sets the candidate's `topic_id`, upserts `topic_weekly_mentions` for
  `(topic_id, current_week)` (+1), updates `canonical_topic.last_seen`.
- On no match: inserts a new `canonical_topic` row (`topic_id` format:
  `TOPIC-<zero-padded sequence>`), sets `first_seen`/`last_seen` to the current week,
  sets the candidate's `topic_id`, inserts its first `topic_weekly_mentions` row.
- Fails loudly on a corrupted/unreadable DB rather than silently proceeding (unchanged
  intent from today's registry corruption guard).

### report.py

- Same JSON/CSV output shape as today (top topics by mention count, top topics by
  week-over-week trend).
- Trend calculation (new/rising/sharply_rising/stable/falling/resurfacing/dormant, with
  the existing recent-weeks-weighted logic) now computed via SQL queries over
  `topic_weekly_mentions` instead of scanning an in-memory dict — same math, same
  `trend_window_weeks` config knob, different data source.

### query.py (new)

- Small standalone CLI for direct, ad hoc DB inspection during development — explicitly
  not a stable API (see Non-goals). Two commands:
  - `python query.py topic <slug>` — prints the canonical topic's trend history and
    linked evidence permalinks.
  - `python query.py search <keyword>` — prints evidence/candidates whose title,
    content, or summary matches the keyword (simple `LIKE` search).

## Data model

SQLite database at `data/pi_agent.db`.

```sql
CREATE TABLE evidence (
  evidence_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  published_at TEXT NOT NULL,
  title TEXT,
  content TEXT,
  metadata TEXT NOT NULL  -- JSON blob
);
-- Immutable: rows are inserted once by fetch.py and never UPDATEd.

CREATE TABLE canonical_topic (
  topic_id TEXT PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  aliases TEXT NOT NULL,  -- JSON array
  first_seen TEXT NOT NULL,  -- ISO week
  last_seen TEXT NOT NULL    -- ISO week
);

CREATE TABLE signal_candidate (
  candidate_id TEXT PRIMARY KEY,
  evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
  signal_type TEXT NOT NULL,
  topic_id TEXT REFERENCES canonical_topic(topic_id),  -- NULL until matched
  summary TEXT NOT NULL,
  confidence REAL NOT NULL
);

CREATE TABLE topic_weekly_mentions (
  topic_id TEXT NOT NULL REFERENCES canonical_topic(topic_id),
  period TEXT NOT NULL,  -- ISO week
  mentions INTEGER NOT NULL,
  PRIMARY KEY (topic_id, period)
);
```

`topic_weekly_mentions` replaces the `weekly_mentions` dict embedded in today's
`registry.json` topic entries — same data, now queryable by period via SQL instead of
requiring a full JSON parse.

Example row values, corresponding to the target spec's section 4/6 examples:

```text
evidence:          EV-2026-000184 | reddit_post | yourproductname | https://reddit.com/... | ...
signal_candidate:  SC-2026-00420  | EV-2026-000184 | usability_issue | TOPIC-0014 | "..." | 0.91
canonical_topic:   TOPIC-0014 | slow-schema-loading | Slow schema loading | ... | 2026-06-04 | 2026-08-22
```

## Migration

- One-time migration script (`migrate_to_sqlite.py`, run once, not part of the ongoing
  pipeline) reads existing `data/registry.json` plus any retained `data/raw/` /
  `data/extracted/` history and populates the new tables, so historical trend data
  survives the cutover.
  - Each existing registry topic becomes one `canonical_topic` row plus one
    `topic_weekly_mentions` row per week in its `weekly_mentions` dict.
  - Existing `example_permalinks` on a topic do not map cleanly to individual evidence
    rows (the old registry didn't retain per-mention evidence), so they are recorded as
    a placeholder `evidence` row per permalink (`source_type='reddit_post'`,
    `metadata={"migrated": true}`) linked to a placeholder `signal_candidate` pointing
    at that topic, so every topic keeps at least one evidence trail post-migration.
- After migration is verified, `data/registry.json`, `data/raw/`, and `data/extracted/`
  are deleted; `data/state.json` is kept as-is.

## Error handling

- Same fail-independently-and-loudly posture as today for each stage.
- `fetch.py`: unchanged — `state.json` untouched on failure, safe retry.
- `extract.py`: unchanged — single post failure logged and skipped, batch continues.
- `match.py` / `report.py` / `query.py`: fail loudly (raise, non-zero exit) on a
  missing or corrupt DB file rather than silently creating an empty one — the DB is the
  one piece of state that accumulates value over time and must not be casually
  clobbered or silently reset.
- All DB writes within a single stage run happen inside one transaction per stage
  invocation, so a mid-run crash leaves the DB at its pre-run state rather than
  partially written.

## Testing

- `match.py`'s merge/create logic and `report.py`'s trend calculations are unit tested
  against a temporary SQLite DB fixture (built via a fixture-seeding helper), replacing
  today's fixture-JSON approach — same coverage intent, different fixture mechanics.
- `fetch.py` / `extract.py` continue to use mocked PRAW / Anthropic clients; assertions
  check the resulting DB rows instead of JSON file contents.
- `test_end_to_end.py` continues to exercise the full CLI pipeline against a temp DB,
  asserting on DB contents and report output.
- The migration script gets its own test: seed a fixture `registry.json`, run the
  migration, assert the resulting DB rows match expectations (including the placeholder
  evidence-row behavior for pre-existing permalinks).

## Open items for implementation planning

- Exact SQL query shapes for `report.py`'s trend calculation (window function vs.
  application-level aggregation) — either is fine, pick whichever keeps the query
  readable.
- Whether `query.py`'s `search` command needs FTS5 or a plain `LIKE` is sufficient at
  current data volume — default to plain `LIKE` given the ad hoc/throwaway nature of
  this tool.
- Sequence-number allocation strategy for `EV-`/`SC-`/`TOPIC-` IDs (e.g. a small
  `id_sequence` table vs. `MAX(id)+1` at insert time) — pick whichever is simplest to
  implement correctly under SQLite's single-writer model.
