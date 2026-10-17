# CLAUDE.md

How this repo is run, for coding agents and for humans who want the short version.

## Commands

- Tests: `pytest`. No network or credentials; every LLM call in tests uses a fake client.
- Lint: `ruff check .`
- Pipeline: `python run_weekly.py [--stage fetch|extract|match|report|materiality]`
- Evals call the real API, cost money and never run in CI. See README "Evals".

## Who designs what

A human designs these. Change them only when asked to, and call the change out in the PR:

- The schema in `db.py`: `SCHEMA_SQL` and `MIGRATIONS`. Data outlives code, and a bad migration corrupts history.
- LLM prompt and response contracts: every `*_PROMPT_TEMPLATE`, the parsers, `SIGNAL_TYPES`.
- The `materiality.py` rule table and thresholds, and the trend math in `report.py` (ADR 003).
- What each stage commits, and when (ADR 005). Model calls go through `ledger.py`; don't call `client.messages.create` from a stage without it.
- Golden labels in `evals/golden/`. A human decides what is correct.

Agent-written code is fine here if tests pass:

- `query.py` output formatting, report and CSV layout, eval console output.
- Test scaffolding and fixtures.
- Docs that describe existing behavior.

## Rules

- Read `docs/decisions/` before changing architecture. If a change contradicts an ADR, stop and ask.
- Fix causes, not symptoms. Before adding code to fix a bug, check whether the fix can remove or simplify code instead. Say which you did in the PR.
- No real API calls in pytest. Inject fake clients the way `tests/test_extract.py` does.
- Pass `encoding="utf-8"` to every file read and write.
- Python 3.11 (CI). No 3.12+ syntax.
- A prompt change changes the eval fingerprints. Run the affected eval and compare runs (README "Run history and drift").
- Schema changes are a new numbered entry in `db.MIGRATIONS`. Never edit `SCHEMA_SQL` and never renumber a shipped migration.
