# Decision

1. Reddit text, and model summaries derived from it, go into prompts between our own tags. The prompt says they are data, and our tag names are escaped inside them.
2. Materiality counts near-duplicate posts in a topic-week once and flags posting bursts. Detection is deterministic, from text and timestamps only.
3. A weekly Claude Opus 5.5 call proposes topic merges and splits into a `review_item` queue. Only a human applies them.

## Context

The pipeline pastes anonymous text into prompts, and its only gate to a material signal is model confidence plus mention counts. A post could instruct the extractor, and a handful of accounts posting the same text could manufacture a "rising" topic. Separately, the README names split and merged topics as the main matching failure, but nothing helped anyone find them.

## Options considered

- **Do nothing.** No tools and no external writes keep the damage small, but signals were still manipulable.
- **Author-based brigading detection.** Rejected: we deliberately store no authors.
- **Let a model merge topics automatically.** Rejected: registry history is human-owned (ADR 006). A wrong automatic merge silently corrupts trends.
- **Chosen:** delimit and measure (injection golden cases); deterministic coordination rules; a review call that only proposes. It is a narrow, stateless call, as ADR 001 anticipates.

## Consequences

- `prompt_injection.yaml` measures injection resistance in every extraction eval.
- Bursts are reported, never subtracted, so real outages still surface. Near-duplicates collapse, so copy-paste campaigns don't.
- The review call uses `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`). If Opus declines, the API re-runs the request on a fallback model in the same call, and the ledger's `parsed.served_by` records which model answered. A refusal from the whole chain is a recorded `response_error`.
- `python review.py list|accept|done|reject`. Accepting a merge goes through `registry.merge_topics`, so history and lineage are kept. Rejected pairs aren't proposed again.
- Off by default (`topic_review: false`).
