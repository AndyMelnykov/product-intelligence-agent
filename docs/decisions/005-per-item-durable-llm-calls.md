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
