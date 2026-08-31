# Decision

Use two independent, stateless LLM calls invoked from deterministic Python control
flow, instead of a tool-using agent loop.

## Context

The pipeline needs the LLM for two genuinely fuzzy jobs: classifying what a Reddit post
is about (`extract.py`), and deciding whether a new signal is the same topic as
something already in the registry (`match.py`). Both are single-shot judgments — there's
no multi-step reasoning, no need for the model to choose between tools, and no
persistent conversation state to maintain across a task.

## Options considered

- **A tool-using agent** (e.g. an agent loop that can query the database, search the
  registry, and decide what to do next). Rejected: nothing here requires the model to
  choose an action or plan multiple steps — it would add a tool-selection failure mode
  and a harder-to-trace execution path for no behavioral benefit.
- **A single combined call** that both classifies and matches in one prompt. Rejected:
  classification runs per-evidence-row as posts arrive; matching needs the *whole*
  week's new candidates plus the full existing registry at once. Different inputs,
  different cadences — combining them would force one call to wait on the other
  unnecessarily.
- **Two narrow, stateless calls** (chosen): `extract_topic()` takes one evidence row and
  returns a fixed-shape JSON object; `_call_matcher()` takes a batch of candidates plus
  the registry and returns one decision per candidate. Each has a fixed prompt template
  and a validated response contract.

## Decision

Keep both calls narrow, stateless, and independently testable. No agent framework, no
shared conversation state, no tool-use loop.

## Consequences

- Each call can be fully exercised in tests with a fixture response
  (`FakeAnthropicClient` in `tests/test_end_to_end.py`) — no live API needed.
- Adding a third kind of judgment (e.g. materiality reasoning) means adding a third
  narrow call, not extending an agent's tool set — consistent with how
  `materiality.py` stayed fully deterministic instead of becoming an LLM call itself
  (see [003](003-deterministic-trend-and-materiality-scoring.md)).
- If a future workflow genuinely needs multi-step reasoning (e.g. "investigate this
  signal across three sources before deciding"), that's a real signal to revisit this
  decision — not before.
