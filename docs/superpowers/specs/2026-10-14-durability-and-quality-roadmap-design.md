# Durability and Quality Roadmap: Design

**Status:** proposed, 2026-10-14
**Source:** an analysis of the Mario Zechner & Armin Ronacher conversation about Pi Durable (durable agent
harnesses, the "effect sandwich", test oracles, cheap classifiers, advisor models), applied to this repo.
Every gap below was checked against the code on `feat/evals-run-history-and-judge-calibration` (commit `2af8169`).

This spec is the shared reference for five implementation plans. It records the problems, the decisions
that hold across plans, and the contracts later plans rely on. Each plan has the details and the code.

---

## 1. Problems found

### 1.1 Durability and idempotency (pipeline correctness and cost)

| # | Where | What happens today | Consequence |
|---|---|---|---|
| D1 | `extract.py:141` | One commit at the end of the stage. | A crash on post 400 of 500 discards 400 paid classifications. |
| D2 | `extract.py:126` | A `skip` result writes nothing. | `get_evidence_without_candidate` returns skipped posts again on every run, and each one is paid for again every week. |
| D3 | `extract.py:120-124` | A response that can't be parsed is printed and forgotten. | The same post is retried on every run forever (a poison message). |
| D4 | `fetch.py:113-115` | The DB commit and the `state.json` write are separate steps. | A crash between them makes the next run re-insert the same posts. |
| D5 | `fetch.py:63-75` + `db.py` schema | The cursor post can be deleted on Reddit, and there is no unique key on the Reddit post id. | If the cursor post disappears, the loop never finds it, re-reads up to `limit` posts and inserts duplicates. Duplicates inflate mentions and can produce false "rising" trends and false material signals. |
| D6 | `materiality.py:170-174` | Signals are committed, then events are appended. | A crash in between loses the event. Re-running the same week duplicates signals and events, because nothing keys them by `(topic, week)`. |
| D7 | `match.py:131` | One transaction for the whole batch; the LLM response is not stored. | Any apply error throws away the paid matching response for the whole week. |

### 1.2 Applying LLM output safely

| # | Where | Failure |
|---|---|---|
| S1 | `match.py:100` | `decision["new_topic"]` raises `KeyError` when the model leaves out an index. |
| S2 | `match.py:111` | A hallucinated `matched_topic_id` violates the foreign key (`foreign_keys=ON`) and rolls back the stage. |
| S3 | `db.insert_canonical_topic` | Two candidates in one batch that propose the same new topic both try to insert its slug. The second hits `DBError` and the week rolls back, even though grouping them is the correct answer. |
| S4 | `extract.py:107-115` | `confidence` is never validated. A string or an out-of-range number reaches `materiality` and corrupts the average. `{"skip": "no"}` counts as a skip because the check is truthiness. |

### 1.3 Provenance and history

- **P1.** `signal_candidate` doesn't record which model or prompt produced it. Eval runs record prompt fingerprints; production rows don't.
- **P2.** Raw model responses are thrown away, so nothing can be replayed, audited, or exported as eval data.
- **P3.** `canonical_topic` is changed in place. There's no history of names or descriptions, no record of merges or splits, and no way to fix a split/merge mistake (the README calls these the main failure mode) without losing history.
- **P4.** `topic_weekly_mentions` is incremented by **run week**, so running `--stage match` a week late puts mentions in the wrong week.

### 1.4 Measurement

- **M1.** Golden sets are the only measurement, and they grow only as fast as a human can label.
- **M2.** Materiality gates on the model's self-reported confidence (≥ 0.85), and nobody has checked whether that confidence is calibrated.

### 1.5 Cost

- **C1.** Every post costs one Sonnet call with adaptive thinking, obvious spam included.

### 1.6 Trust

- **T1.** Untrusted Reddit text is pasted into prompts with no delimiters. A post can tell the extractor which `signal_type` and `confidence` to output.
- **T2.** Several accounts posting near-identical text in a short burst (brigading) counts as several independent mentions.
- **T3.** The README says new topics need human review, but nothing in the system supports that review.

### 1.7 Process

- **X1.** No written rule says which layers a human designs and where agent-written code is fine. Bug-fix PRs aren't checked for "could this have removed code?"

---

## 2. Goals and non-goals

**Goals**

1. Re-running any stage, or the whole pipeline, after a crash at any point gives the same final state as an uninterrupted run, and costs at most one repeated LLM call per stage.
2. No model output, however malformed, can crash a stage or corrupt the registry.
3. Every derived row can be traced to the model call that produced it, along with its prompt fingerprint and raw response.
4. Registry history (versions, merges, splits) is kept and can be queried "as of week W".
5. Quality checks that need no labels sit next to the golden sets.
6. Spam stops being re-billed every week, and can optionally be filtered by a cheap model, with the accuracy cost measured.
7. Untrusted input is clearly delimited, and coordinated posting is flagged before it can create a material signal.
8. A human review queue for suspected topic merges and splits.

**Non-goals** (deliberately out of scope)

- **Agent loop or tool use.** ADR 001 stands. The triage call (Plan 4) and the review call (Plan 5) are additional narrow, stateless calls, which ADR 001 explicitly anticipates.
- **Built-in scheduler or webhooks.** This work makes unattended runs safe; it doesn't add the scheduler.
- **Moving to structured outputs (`output_config.format`).** It would remove a class of parse errors, but it changes every prompt contract and invalidates eval comparability. Revisit after Plan 3's fuzzing shows how much parse failure remains.
- **Message Batches API.** Halves the cost but needs asynchronous result collection; revisit after Plan 4.
- **A multi-tenant or multi-process writer.** The pipeline assumes one process at a time, as it does today. Ledger recovery relies on that.
- **Storing authors.** Coordination detection (Plan 5) uses only text and timestamps, keeping the privacy property in the README.

---

## 3. Phases

| Plan | File | Covers | Depends on | ADR |
|---|---|---|---|---|
| P1 | `plans/2026-10-14-p1-durable-pipeline-implementation.md` | D1-D7, S1-S3, P1, P2, X1 | none | 005 |
| P2 | `plans/2026-10-14-p2-versioned-topic-registry-implementation.md` | P3, P4 | P1 | 006 |
| P3 | `plans/2026-10-14-p3-label-free-oracles-implementation.md` | M1, M2, S4 | P1 (Tasks 6-7: `match.resolve_decisions`) | none (eval tooling) |
| P4 | `plans/2026-10-14-p4-extraction-triage-cascade-implementation.md` | C1 | P1, P3 Task 1 (token and confidence capture) | 007 |
| P5 | `plans/2026-10-14-p5-review-queue-and-untrusted-input-implementation.md` | T1, T2, T3 | P1, P2 (merge operation) | 008 |

P1 goes first and is the largest: everything else writes to its ledger. P3 can run in parallel with P2.

**Migration numbering rule.** Each plan's tasks add numbered schema migrations (§4.1). The plans assume the order above: P1 uses 1-5, P2 uses 6-7, P5 uses 8. P3 and P4 add no migrations. If you execute plans in a different order, give each new migration the **next free number** at the time you implement it. Never renumber a migration that has already run on a real database.

---

## 4. Cross-plan contracts

Later plans depend on these exact names. A plan that changes one must update every plan that uses it.

### 4.1 Schema migrations (P1 Task 2)

- `db.MIGRATIONS: list[tuple[int, Callable[[sqlite3.Connection], None]]]`, ascending by version.
- `db.init_db(conn)` runs `SCHEMA_SQL` (the frozen v0 baseline, never edited again), then `db.apply_migrations(conn)`.
- `db.apply_migrations(conn, migrations=None)`: runs each migration whose version is above `PRAGMA user_version`, each in its own transaction, and sets `user_version` in that same transaction.
- `db._add_column(conn, table, column_sql)`: an `ALTER TABLE ... ADD COLUMN` that does nothing if the column already exists.
- `db.utc_now() -> str`: ISO-8601 UTC timestamp to the second.

### 4.2 Fingerprints (P1 Task 2)

- `fingerprints.fingerprint(value) -> str` (12 hex chars). It moves out of `evals/runs.py`, which keeps the name `runs.fingerprint` by importing it, so eval code doesn't change.

### 4.3 Model replies (P1 Task 3)

```python
@dataclass(frozen=True)
class LLMReply:            # llm.py
    text: str | None       # first text block, None if there is none
    stop_reason: str | None
    input_tokens: int | None
    output_tokens: int | None

def reply_from_response(response) -> LLMReply
```

### 4.4 The LLM call ledger (P1 Task 3)

Table `llm_call`: `call_id INTEGER PK AUTOINCREMENT, stage, subject_key, model, prompt_fingerprint, request_meta (JSON), status, raw_response, parsed (JSON), error, stop_reason, input_tokens, output_tokens, started_at, finished_at, applied_at`.

- Stages: `extract`, `match`, `triage` (P4), `review` (P5).
- Statuses: `pending` → `succeeded` | `api_error` | `response_error`; `pending` → `abandoned` (set by the next run).
- `ledger.start_call(conn, *, stage, subject_key, model, prompt_template, request_meta=None) -> int` **commits**.
- `ledger.finish_call(conn, call_id, *, status, reply=None, parsed=None, error=None)` does **not** commit; the caller commits it together with the rows derived from it.
- `ledger.abandon_stale_calls(conn, stage) -> int` commits.
- `ledger.mark_applied(conn, call_id)` does not commit.
- `ledger.get_call(conn, call_id) -> dict | None`, `ledger.get_unapplied_calls(conn, stage) -> list[dict]`. Both JSON-decode `request_meta` and `parsed`.

**The effect sandwich, as used by every model-calling stage:** `start_call` (intent, committed) → API call (effect) → `finish_call` + derived writes + `commit` (result). The Anthropic API has no idempotency key, so a call interrupted mid-flight is marked `abandoned` and made again: at most one repeated call per stage per crash.

### 4.5 Evidence identity and extraction outcome (P1 Tasks 4-5)

- `evidence.source_key` (`"reddit_post:<id>"`, partial unique index) and `evidence.duplicate_of`.
- Table `evidence_extraction(evidence_id PK, status, attempts, decided_by, last_call_id, updated_at)`.
  - `status` is one of `candidate | skipped | retryable | dead`.
  - `decided_by` is one of `extractor | triage | backfill`.
- `db.get_evidence_pending_extraction(conn)`: not a duplicate, and either no outcome yet or `retryable`.

### 4.6 Match resolution (P1 Task 6)

```python
@dataclass
class Resolution:          # match.py
    index: int
    action: str            # "match" | "create" | "invalid"
    topic_id: str | None = None
    new_topic: dict | None = None   # {"name", "slug", "description"}, slug normalized
    reason: str | None = None

def resolve_decisions(candidates, decisions, existing_topics, redirects=None) -> list[Resolution]
```

`redirects` (`{merged_topic_id: surviving_topic_id}`) is added by P2 Task 3. It is a pure function: it never raises, whatever `decisions` contains.

### 4.7 Config keys (P4, P5)

`config.yaml` gains two optional booleans, both defaulting to `false`: `extraction_cascade` (P4) and `topic_review` (P5).

### 4.8 Models

| Use | Model ID | Why |
|---|---|---|
| Extraction, matching | `claude-sonnet-5` (unchanged) | Changing the production model is a separate, eval-gated decision. |
| Summary judge | `claude-opus-5` (unchanged) | Its rubric is calibrated against this model. |
| Paraphrase generation (P3) | `claude-sonnet-5` | Same family as extraction; meaning-preserving rewrites. |
| Triage (P4) | `claude-haiku-5-5`, `effort: low` | $0.10 / $0.50 per MTok, against Sonnet 5's $2 / $10 (~20× cheaper). Adaptive thinking is on by default. |
| Topic review (P5) | `claude-opus-5-5`, `effort: high`, `fallbacks: "default"` | One call a week, so quality matters more than price. Server-side refusal fallback is enabled (beta `server-side-fallback-2026-07-01`; SDK 0.122 accepts `fallbacks="default"`). |

**Cascade guardrail.** Before adding a second model, P4 Task 1 measures the cheaper alternative first: the same Sonnet model at `effort: low`. The cascade ships only if it beats that baseline on cost at an equal false-skip rate.

---

## 5. Per-phase design decisions

### P1: durable pipeline

1. **One transaction per unit of work, not per stage.**
   - Extraction commits per evidence row.
   - Matching commits the stored response first, then the applied decisions together with `applied_at`.
   - Fetch commits per subreddit.
   - Materiality commits signals together with their outbox rows.
2. **Skips and failures are outcomes.**
   - A skip is recorded and never re-sent.
   - A response error counts toward `MAX_EXTRACTION_ATTEMPTS = 3`, then the row is `dead`.
   - API errors don't count toward attempts (nothing was paid for). After `MAX_CONSECUTIVE_API_ERRORS = 5` the stage stops and raises, so an outage doesn't sweep the whole backlog.
3. **Fetch idempotency** comes from a natural key (`reddit_post:<id>`), not from the cursor. The cursor becomes an optimization. It moves into SQLite (`fetch_cursor`); `state.json` is read once as a legacy fallback and never written again.
4. **Existing duplicates.** The migration keeps the first row per Reddit id and marks the rest `duplicate_of`. Duplicates are excluded from future extraction, and from mention counts once P2 makes counts derived.
5. **Matching applies only what's valid, per candidate.** Invalid decisions increment `match_attempts`, store `match_error` and leave the candidate unmatched. Each run makes one narrow retry round with only those candidates. After `MAX_MATCH_ATTEMPTS = 3` a candidate is "stuck" and shows up in `query.py health`.
6. **Slug repair rules.**
   - A slug is normalized to `[a-z0-9]+(-[a-z0-9]+)*`.
   - A "new" topic whose slug already exists becomes a match to that topic.
   - Two candidates proposing the same new slug in one batch share one new topic.
7. **Events use an outbox.**
   - `event_outbox` rows are written in the same transaction as the signal.
   - `flush_outbox` runs at the start and end of materiality, appends lines, and skips any `signal_id` already in `events.jsonl`. This gives exactly-once delivery for consumers that read the file.
   - One signal per `(topic, week)`; a re-run inserts again only if the label escalates.

### P2: versioned registry

1. Every change to a topic's definition writes a `topic_version` row. `canonical_topic` stays the "latest" view, so existing readers don't change.
2. Merges and splits are explicit operations (`registry.py`), recorded in `topic_lineage`.
   - Candidates move to the surviving topic but keep `original_topic_id`, which makes undo and "as of" views possible.
   - A merged topic gets `status = 'merged'` and `merged_into`, and is dropped from the matcher's registry. Its name becomes an alias of the survivor, and the matcher now sees aliases.
3. **Mention counts are derived.** `topic_weekly_mentions` becomes a cache, rebuilt per touched topic from non-duplicate candidates, keyed by the ISO week of `captured_at` (the same week materiality uses). `first_seen`/`last_seen` also follow evidence weeks, and only ever widen.
4. **Two read modes:** "latest" (`canonical_topic`, used by reports) and "as of week W" (`registry.registry_as_of`, used for exports and history). Reports stay on "latest"; that's the view that answers "what is happening".
5. Production data becomes matching-eval scenarios through an export script. Its drafts carry `draft: true`, which the golden loader rejects, so unreviewed labels can't be committed (the same idea as `human_verdict: null` for judge cases).

### P3: label-free oracles

1. **Metamorphic checks for matching:**
   - **identity:** the same input twice, which measures the model's own noise;
   - **order:** shuffled candidates;
   - **self-match:** a candidate that created a topic must match that topic once it exists.
   Results are reported relative to the identity noise floor.
2. **Paraphrase stability for extraction.** Paraphrases are generated once and cached in `evals/golden/paraphrases/`, so runs can be compared. A paraphrase's `skip` and `signal_type` are compared with the model's own answer on the original, so no labels are needed.
3. **Confidence calibration** is computed in every extraction eval report, in bins aligned with `materiality.CONFIDENCE_MEDIUM_THRESHOLD` and `CONFIDENCE_HIGH_THRESHOLD`.
4. **Parser fuzzing** with Hypothesis lives in pytest (deterministic, no API). It pins S4 and the P1 resolution guarantees. Hypothesis is a new dev dependency.

### P4: triage cascade

1. Measure `effort: low` on the existing extractor first.
2. Triage only ever skips; it never classifies. A skip is trusted only when `route == "skip"` and `confidence >= TRIAGE_SKIP_THRESHOLD` (default 0.9, chosen by the threshold sweep). If triage fails, the post goes to the extractor (fail open).
3. A deterministic 5% audit sample of triage skips still goes to the extractor. `query.py triage-audit` reports disagreements: a false-skip rate measured in production, without labels.
4. Off by default (`extraction_cascade: false`). Turning it on is a recorded decision after the sweep (P4 Task 5).

### P5: review queue and untrusted input

1. **Injection golden cases come first** (baseline), then delimiting:
   - post text goes inside `<post_title>`/`<post_body>`, with a statement that it is data;
   - our own tag names are escaped inside the content;
   - the matching prompt wraps candidate summaries the same way, since they derive from user text.
   The judge prompt is left alone, so judge calibration stays valid.
2. **Coordination detection is deterministic and has no authors:**
   - near-duplicate clusters (normalized-text similarity ≥ 0.85) collapse to one effective mention for materiality;
   - bursts (≥ 4 posts within 2 hours) are flagged in the reasons but not suppressed, because outages create legitimate bursts.
   Trend math is unchanged.
3. **The review queue:**
   - A weekly Opus call lists suspected merges and splits involving topics that were new or touched this week. Items are stored in `review_item`.
   - A human accepts or rejects them with `review.py`. Accepting a merge calls `registry.merge_topics`; splits are always done by hand with `registry.py split`.
   - The model never changes the registry.
   - Previously rejected pairs aren't suggested again.
   - Off by default (`topic_review: false`).

---

## 6. Risks and open questions for the owner

1. **Hypothesis dependency (P3).** It adds a dev-only package. The alternative is hand-written fuzz loops with `random`, which are weaker at shrinking failures. Recommended: accept.
2. **History changes when counts become derived (P2).** Old weekly counts were keyed by run week. Rebuilding re-keys them by evidence week, so past reports regenerated afterwards can differ slightly. The rebuild prints a before/after diff, and you decide whether to run it on the real DB.
3. **Production data in the repo (P2 Task 5).** Exported scenarios contain model-written summaries of real posts (no usernames). Review them before committing, or keep them in a gitignored folder.
4. **Opus 5.5 for review (P5).** Roughly one call a week. With `fallbacks: "default"`, a refused request is re-run on another model in the same call; the ledger records which model answered (`stop_reason`, usage).
5. **Turning features on.** Both `extraction_cascade` and `topic_review` stay off until you've read their measurement output. No plan turns them on automatically.
