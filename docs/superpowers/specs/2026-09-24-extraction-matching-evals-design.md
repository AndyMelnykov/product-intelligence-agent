# Extraction & Matching Evals — Design

## Purpose

`extract.py` and `match.py` are the two pipeline stages that call Claude to make
judgment calls (what signal is this post making, is this the same topic as an existing
one). The existing test suite (`tests/test_extract.py`, `tests/test_match.py`) verifies
the code's handling of *given* responses via a hand-scripted `FakeClient` — it says
nothing about whether the real model, given real Reddit text, extracts the right signal
or matches topics correctly. This design adds a golden-set eval suite that runs the real
prompts against the real API and grades the output, so prompt/model changes can be
checked against a fixed, human-verified bar and the bar can grow over time.

## Non-goals

- Not a CI gate. Evals hit the real Anthropic API (cost, latency, non-determinism) and
  are run manually or on a schedule, never as part of the GitHub Actions workflow or the
  pytest suite.
- Not a historical trend-tracking system. v1 prints a report to the console; persisting
  results across runs to track pass-rate drift over time is a possible future addition,
  not part of this design.
- Not covering `fetch.py`, `report.py`, or `materiality.py` — none of them call Claude
  (confirmed: only `extract.py`, `match.py`, and `credentials.py` import `anthropic`), so
  there's no model judgment to eval there.
- Not a sequential simulation of the registry evolving over weeks. Matching examples are
  independent, static scenarios (a topic-registry snapshot + candidates + expected
  decision), not a multi-week narrative.

## Architecture

```
evals/
  golden/
    extraction/
      database_development.yaml   # one file per product area
    matching/
      dark_mode_duplicate.yaml    # one file per scenario
  harness.py                      # golden-set loading, grading, aggregation
  judge.py                        # LLM-as-judge for the extraction summary field
  run_extraction_eval.py          # CLI entry point
  run_matching_eval.py            # CLI entry point
```

Both runners call the actual production code (`extract.extract_topic`,
`match.build_matching_prompt` + the matcher call in `match.py`) against a real
`anthropic.Anthropic()` client, obtained the same way production does — via
`credentials.get_secret("anthropic_api_key")`. This is deliberate: the eval exercises the
real prompt text and real parsing logic, not a reimplementation of it. Nothing in
`extract.py` or `match.py` needs to change to support this — the eval suite is a new
consumer of their existing public functions.

### `evals/harness.py`

- `load_golden_dir(path) -> list[Example]` — reads all YAML files in a golden directory,
  validates each against the expected schema, raises immediately with the offending
  file/example id on a schema violation.
- `grade_extraction_example(example, client) -> ExampleResult` — calls
  `extract.extract_topic(client, example.evidence)`, exact-matches `skip`, `signal_type`,
  `entity`, `effective_date` against `example.expected`, and delegates the `summary`
  field to `judge.judge_summary(...)`.
- `grade_matching_scenario(scenario, client) -> ExampleResult` — builds candidates/
  existing-topics from the scenario, runs them through `match.py`'s matcher, exact-matches
  each candidate's `matched_topic_id`/`new_topic` against `scenario.expected`.
- `aggregate(results) -> Report` — per-field accuracy, a confusion matrix for
  `signal_type` misses, overall judge pass rate, and the list of individual failures
  (expected vs. actual) for console printing.

### `evals/judge.py`

- `judge_summary(evidence, reference_summary, actual_summary, client) -> JudgeVerdict`
  — one Claude call with a rubric prompt: given the original evidence text and the
  human-written reference summary, is the model's actual summary a faithful, on-topic
  description that doesn't hallucinate or drift off the reference's meaning? Returns a
  pass/fail plus a short reason string, so a failure is legible in the report without
  re-reading the raw evidence.

### CLI entry points

- `python evals/run_extraction_eval.py [--filter database_development]`
- `python evals/run_matching_eval.py [--filter dark_mode_duplicate]`

`--filter` matches on the golden file's basename (extraction) or scenario `name`
(matching), so you can re-run just the set you're iterating on instead of the whole
suite.

## Golden set format

**Extraction** (`evals/golden/extraction/database_development.yaml`):

```yaml
product_area: database_development
examples:
  - id: db-001
    evidence:
      title: "Postgres migration tool keeps timing out on large tables"
      content: >
        Every time I try to run a migration on our biggest table (40M rows) it just
        hangs and eventually times out. No error message, just nothing.
    expected:
      skip: false
      signal_type: reliability_issue
      entity: null
      effective_date: null
    reference_summary: >
      User reports the migration tool times out on large tables with no error message.
    notes: >
      Clear reliability complaint; no company/product named, so entity must be null.

  - id: db-002
    evidence:
      title: "Check out my new crypto trading bot!!!"
      content: "Link in bio, DM me for early access"
    expected:
      skip: true
    notes: "Off-topic spam; extractor should skip it entirely."
```

When `expected.skip` is `true`, no other `expected` fields or `reference_summary` are
required — grading stops at the skip check.

**Matching** (`evals/golden/matching/dark_mode_duplicate.yaml`):

```yaml
name: dark_mode_duplicate
existing_topics:
  - topic_id: TOPIC-0001
    name: "Connection pooling exhaustion under load"
    description: "Users hitting max-connections errors under concurrent load"
candidates:
  - signal_type: reliability_issue
    summary: "App runs out of DB connections during traffic spikes"
expected:
  - index: 0
    matched_topic_id: TOPIC-0001
    new_topic: null
notes: >
  Same underlying issue phrased differently; should match the existing topic rather
  than spawn a duplicate.
```

A scenario can include multiple candidates (matching some existing topics, some new) to
exercise the batch-matching prompt the way production actually calls it.

## Grading & reporting

For each example: run the real pipeline function, exact-match the categorical fields,
judge-grade the summary (extraction only), and record pass/fail with the specific field
that diverged. At the end, print an aggregate report: per-field accuracy, a
`signal_type` confusion matrix (which types get confused for which), overall judge pass
rate, and a listing of every failing example with expected vs. actual side by side. The
suite has no overall pass/fail exit code for grading outcomes — it's a signal to read,
not a gate — but exits non-zero immediately if a golden file fails schema validation,
since a malformed golden example is a bug in the eval data itself, not a model result.

## Error handling

- A per-example API error (rate limit, transient failure) is caught, logged, and the
  example is marked `errored` — distinct from `failed` — so one flaky call doesn't
  invalidate the run or get miscounted as a model mistake.
- Malformed golden YAML (missing required key, invalid `signal_type`, bad shape) fails
  fast at load time with the file name and example id in the error, before any API calls
  are made — no point spending API budget on a run that can't be graded.

## Testing

- `harness.py`'s exact-match grading, aggregation, confusion-matrix logic, and the golden
  YAML schema validator are plain deterministic Python — unit tested with pytest against
  fixture data, following this repo's existing `FakeClient` pattern (see
  `tests/test_extract.py`). These tests live in `tests/` alongside the rest of the suite
  and run in CI as normal, since they don't call any real API.
- `judge.py`'s response-parsing logic (turning the judge's raw text into a
  `JudgeVerdict`) is unit tested the same way, with a fake client returning
  scripted judge responses.
- The eval suites themselves (`run_extraction_eval.py`, `run_matching_eval.py`) are not
  unit tested — they're the tool you run manually, not code under test by another test.

## Open items for implementation planning

- Exact wording of the judge rubric prompt in `judge.py`.
- Whether/how to persist run results (e.g. a timestamped JSON under a gitignored
  `evals/results/`) for comparing pass rates across runs — default to console-only
  output for v1, revisit if tracking drift over time becomes valuable.
- Final CLI flag surface — `--filter` is required; a `--model` override for testing a
  different model than production's `EXTRACTION_MODEL`/`MATCH_MODEL` is a likely
  addition but not required for v1.
- The initial golden set content: ~20 real Reddit posts for `database_development`
  (evidence + manually verified expected labels) and a handful of matching scenarios
  derived from topics that emerge in that same set. This is data-authoring work, not
  code, and can proceed in parallel with or after the harness implementation.
