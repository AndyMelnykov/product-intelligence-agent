# Decision

1. TeamOSTemplate is the consumer of this repo's material signals. Its hypothesis layer fills the role the vision doc calls the Strategic Signals Agent.
2. An optional `publish` stage hands this week's material topics to a local TeamOSTemplate checkout by calling its `scripts/signals/signals.py add` CLI.
3. `data/events.jsonl` stays as an optional event feed (ADR 004). It is no longer the only way out.

## Context

ADR 004 stopped at emitting an event because the consumer, a separate Strategic Signals Agent, did not exist, and nothing read `events.jsonl`. TeamOSTemplate now has a signals file (`signals.csv`), a contract for what a signal row may contain, and a validating CLI that appends to it. Hypotheses, and any decision about what a signal means for the business, live there.

## Options considered

- **Keep waiting for a separate Strategic Signals Agent.** Rejected: the hand-off stays one-way and unused, and TeamOSTemplate already does that agent's job.
- **Import TeamOSTemplate's Python code.** Rejected: couples the two repos' internals and versions.
- **Call its CLI** (chosen). `signals.py` validates every row and skips duplicates, so this repo does not need to know the file format.

## Consequences

- ADR 004's "no consumer exists" no longer holds. Its rule still does: this repo never decides a business action. Hypotheses stay in TeamOSTemplate.
- First time the pipeline writes outside this repo. It does so only through the checkout's validated CLI, and never runs git. Reviewing the `signals.csv` diff and committing it is a human step.
- Reddit text goes out as `detail`, which TeamOSTemplate treats as quoted data (ADR 008's stance). Usernames and emails are replaced before it leaves, and agent-internal ids (`EV-…`, `SC-…`, `SIG-…`) are never written.
- No model calls and no ledger entry (ADR 005). `publish` reads the DB and commits nothing.
- Idempotent through `signals.py`'s rule of skipping an equal `(source_ref, summary)`. There is no bookkeeping table, so `query.py health` does not show publication state; adding one is a migration and a human decision.
- One signal per signal candidate, newest first, capped at `max_signals_per_topic` (default 10) per topic. Drilling past the cap needs the Integration API (vision §11), which no longer blocks the hand-off.
- "That week" is `evidence.captured_at` in the ISO week, the same definition materiality used. Relative Reddit permalinks become `https://www.reddit.com/…` URLs, so `source_ref` is always findable.
- Off by default. `--stage publish` without a `teamos` section is an error; `--stage all` skips it with one line.
