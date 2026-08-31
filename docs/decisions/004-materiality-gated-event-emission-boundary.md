# Decision

Emit a `product_intelligence.signal.material` event for high/critical topics and stop —
never decide or recommend a specific action beyond `strategic_assessment` /
`urgent_strategic_assessment`.

## Context

This repo is one slice of a larger two-system design described in
[docs/product-intelligence-agent-vision.md](../product-intelligence-agent-vision.md):
a Product Intelligence Agent that turns evidence into signals, and a (separate,
not-yet-built) Strategic Signals Agent that decides what to do about them. Every stage
up to and including materiality scoring lives in this repo. What happens after a signal
is emitted does not.

## Options considered

- **Also generate a recommended response** (e.g. "raise priority," "notify sales,"
  "update roadmap"). Rejected: deciding what a signal means for the business requires
  context this repo doesn't have — competitor state, roadmap priorities, customer
  segment value — and conflating evidence-gathering with response-planning is exactly
  what the vision doc's system-boundary diagram warns against.
- **Include full evidence text in the emitted event**, so a downstream consumer never
  needs to call back into this repo. Rejected: it duplicates data that's already
  durably stored (`evidence` table), bloats the event log, and creates two sources of
  truth for the same evidence row.
- **Emit a minimal event with a signal ID and evidence IDs** (chosen) — `emit_event()`
  in `materiality.py` writes `signal_id`, `signal_type`, `summary`, `confidence`,
  `materiality`, and `evidence_ids` to `data/events.jsonl`; a consumer that needs the
  full evidence text looks it up separately (today via `query.py` or direct SQLite
  access — see the Integration API item in the README's Roadmap).

## Decision

`materiality.py` only ever appends a lightweight event envelope and stops.
`recommended_next_step` is always one of `strategic_assessment` or
`urgent_strategic_assessment` — never a specific business action.

## Consequences

- This repo can be evaluated purely on evidence quality (did it find the right things,
  classify them correctly, and flag the important ones) without also being on the hook
  for whether a *business* judgment call downstream was right.
- Nothing in this repo currently reads back its own `data/events.jsonl` — there is no
  consumer implemented yet. The event contract is speculative until a Strategic Signals
  Agent (or an equivalent consumer) actually exists.
- If a future requirement needs this repo to recommend a specific action rather than
  just "assess this," that's a deliberate scope change to flag and design for — not
  something to add quietly inside `materiality.py`.
