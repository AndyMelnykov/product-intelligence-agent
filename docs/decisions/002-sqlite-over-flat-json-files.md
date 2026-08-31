# Decision

Store all pipeline state in a single SQLite database (`data/pi_agent.db`) instead of
per-week flat JSON files.

## Context

The original pipeline stored raw fetched posts, extracted topics, and the canonical
topic registry as separate JSON files per week (`data/raw/<week>/...`,
`data/extracted/<week>.json`, `data/registry.json`). This worked while the registry was
small, but made cross-week queries (e.g. "how many total mentions has this topic ever
had") require reading and merging every week's file by hand, and gave no way to enforce
referential integrity between an extracted signal and the evidence it came from.

Full reasoning and the migration itself are in
[docs/superpowers/specs/2026-08-25-evidence-data-model-migration-design.md](../superpowers/specs/2026-08-25-evidence-data-model-migration-design.md).

## Options considered

- **Keep flat JSON files, add an in-memory index.** Rejected: doesn't solve
  referential integrity (nothing stops an orphaned `signal_candidate` from pointing at
  a deleted evidence file), and every stage would still need to hand-roll file
  locking for concurrent-safe writes.
- **A hosted database (Postgres, etc.).** Rejected: this is a single-machine, single-user
  pipeline with no concurrent writers — an external database service is operational
  overhead with no corresponding benefit here.
- **SQLite** (chosen): one file, no server process, transactional writes, and foreign
  keys between `evidence`, `signal_candidate`, `canonical_topic`, and `material_signal`
  (see `db.py`'s `SCHEMA_SQL`).

## Decision

Migrate to a single SQLite file with normalized tables and foreign-key references
between evidence, extracted candidates, canonical topics, and material signals.

## Consequences

- Every pipeline stage (`fetch`, `extract`, `match`, `report`, `materiality`) wraps its
  writes in one transaction per run and rolls back entirely on any exception — a failed
  run never leaves a half-written state (see the `try/except: conn.rollback()` pattern
  in each module's `run()`).
- `query.py` can answer cross-week questions (total mentions for a topic, full-text
  search across evidence and candidates) with plain SQL instead of merging files.
- Evidence rows are immutable by convention (nothing in the codebase updates or deletes
  an `evidence` row after insert) — the same immutability guarantee the original
  vision doc calls for, now enforced by schema rather than by file-naming convention.
