# P3: Label-Free Oracles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure extraction and matching quality in ways that need no human labels, so measurement grows faster than the golden sets:

- confidence calibration;
- metamorphic matching checks;
- paraphrase stability;
- property-based fuzzing of every response parser.

Also close the parser gaps the fuzzing pins.

**Architecture:**
- The eval harness records confidence and token usage per example and reports calibration in bins aligned with the materiality thresholds.
- A new `evals/oracles.py` runs metamorphic checks (identity, order, self-match) on the matching scenarios, and paraphrase-stability checks on the extraction examples. Paraphrases come from a cache in `evals/golden/paraphrases/`.
- Oracle results use the existing `ExampleResult` / `FieldResult` / `Report` types, so aggregation, run records and `compare_runs.py` work unchanged.
- Hypothesis property tests in pytest pin the parser guarantees with no API calls.

**Tech Stack:** Python 3.11, Anthropic SDK, PyYAML, pytest, **Hypothesis** (new dev dependency), ruff.

**Spec:** [docs/superpowers/specs/2026-10-14-durability-and-quality-roadmap-design.md](../specs/2026-10-14-durability-and-quality-roadmap-design.md), sections 1.2 (S4), 1.4 (M1, M2), 5 "P3". **Requires P1 merged** (`extract.request_extraction`, `extract.parse_extraction_reply`, `extract.parse_extraction_text`, `match.resolve_decisions`). P2 is not required.

## Global Constraints

- Evals are **not a CI gate**. Nothing under `evals/` is named `test_*.py`. Tests inject fake clients; no pytest test calls the API.
- Runners call the real production functions (`extract.extract_topic` / `request_extraction`, `match.call_matcher`). No copied prompt text in `evals/`.
- Exit codes follow the existing runners: 0 whenever a report is printed; 2 for malformed golden data, a `--filter` that matches nothing, missing credentials, or nothing to run.
- Every file read and write passes `encoding="utf-8"`. Console scripts call `sys.stdout.reconfigure(errors="backslashreplace")`.
- `format_report` output stays ASCII apart from model text (an existing test asserts `text.encode("ascii")`).
- Python 3.11, ruff (E, F, W).
- The paraphrase generator is `claude-sonnet-5`. Production models don't change.

## Review Focus

1. **The model's own noise looks like an order or self-match failure.** Expected: every matching scenario also runs an `identity` check (the same input twice), reported beside the others, and the README says to read order and self-match against it. Pinned in Task 3 (`test_identity_noise_is_reported_separately`).
2. **A paraphrase that changes the meaning makes a correct extractor look unstable.** Expected: paraphrases are cached in a committed file with a header telling the reviewer to delete bad ones; generation only happens with `--generate-missing`. Pinned in Task 4 and Task 5.
3. **A model returns `"confidence": "high"` or `1.7`, or `"skip": "no"`.** Expected: the first two are response errors; the last is not a skip. Pinned in Task 2.
4. **Old run files saved before this plan** (no `confidence` on results, no `calibration` in the summary). Expected: `compare_runs.py` still compares them. Pinned in Task 1 (`test_compare_runs_accepts_records_without_new_fields`).
5. **An oracle run with no cached paraphrases.** Expected: exit 2 with a message naming `--generate-missing`, and no API spend. Pinned in Task 5.

---

## File Structure

| File | Responsibility |
|---|---|
| `evals/harness.py` (modify) | `ExampleResult.confidence/input_tokens/output_tokens`; `calibration`, `calibration_error`; token totals; report sections |
| `evals/runs.py` (modify) | Summary gains `calibration` and `tokens`; `KINDS` gains `oracle_matching`, `oracle_extraction` |
| `extract.py` (modify) | Stricter `parse_extraction_text` (confidence, summary, skip, effective_date) |
| `evals/oracles.py` (create) | `Placement`, matching metamorphic checks, extraction stability check |
| `evals/paraphrase.py` (create) | Paraphrase generation, cache load/save |
| `evals/run_oracle_eval.py` (create) | CLI runner for both oracle kinds |
| `requirements-dev.txt` (modify) | `hypothesis` |
| `README.md` (modify) | Evals: calibration, oracles |
| `tests/test_response_fuzz.py`, `tests/test_evals_oracles.py` (create); `tests/test_evals_harness.py`, `tests/test_evals_runs.py`, `tests/test_evals_runners.py`, `tests/test_extract.py`, `tests/test_evals_golden.py` (modify) | Tests |

**Branching:** `git checkout main && git pull && git checkout -b feat/label-free-oracles`.

---

### Task 1: Confidence calibration and token usage in eval reports

**Files:**
- Modify: `evals/harness.py`
- Modify: `evals/runs.py` (`build_run_record` summary)
- Test: `tests/test_evals_harness.py` (append), `tests/test_evals_runs.py` (update one assertion, append one test)

**Interfaces:**
- Consumes: `extract.request_extraction`, `extract.parse_extraction_reply` (P1); `materiality.CONFIDENCE_MEDIUM_THRESHOLD`, `CONFIDENCE_HIGH_THRESHOLD`.
- Produces:
  - `harness.ExampleResult(..., confidence=None, input_tokens=None, output_tokens=None)`
  - `harness.Report(..., calibration=[], tokens={})`
  - `harness.CALIBRATION_BINS`
  - `harness.calibration(results, bins=CALIBRATION_BINS) -> list[dict]`, each row with keys `low`, `high`, `count`, `mean_confidence`, `accuracy`
  - `harness.calibration_error(rows) -> float | None`
  - Run-record `summary` gains `calibration` (the rows) and `tokens` (`{}` or `{"calls", "input", "output"}`). P4 relies on `tokens`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evals_harness.py`:

```python
class Usage:
    input_tokens = 300
    output_tokens = 40


def cal_result(confidence, correct, status="passed"):
    field = harness.FieldResult("signal_type", "signal_type", "a", "a" if correct else "b", correct)
    return harness.ExampleResult("x", "s", status, [field], confidence=confidence)


def test_grade_extraction_records_confidence_and_tokens():
    response = FakeResponse(extraction_response(confidence=0.72))
    response.usage = Usage()
    client = FakeClient([])
    client.messages.create = lambda **kw: response if not kw["model"].startswith("claude-opus") else FakeResponse(JUDGE_PASS)

    result = harness.grade_extraction_example(EXAMPLE, client)

    assert (result.confidence, result.input_tokens, result.output_tokens) == (0.72, 300, 40)


def test_skipped_example_has_no_confidence():
    result = harness.grade_extraction_example(SKIP_EXAMPLE, FakeClient([json.dumps({"skip": True})]))
    assert result.confidence is None


def test_calibration_bins_signal_type_accuracy_by_stated_confidence():
    skip_only = harness.ExampleResult("s", "s", "passed", [harness.FieldResult("skip", "skip", True, True, True)])
    results = [cal_result(0.95, True), cal_result(0.9, False), cal_result(1.0, True), cal_result(0.7, True),
               cal_result(0.3, False), cal_result(0.99, True, status="errored"), skip_only]

    rows = harness.calibration(results)

    assert [(r["low"], r["high"], r["count"]) for r in rows] == [(0.0, 0.6, 1), (0.6, 0.85, 1), (0.85, 1.0, 3)]
    assert rows[2]["accuracy"] == pytest.approx(2 / 3)
    assert rows[2]["mean_confidence"] == pytest.approx((0.95 + 0.9 + 1.0) / 3)
    assert harness.calibration_error(rows) == pytest.approx(
        0.2 * 0.3 + 0.2 * 0.3 + 0.6 * abs(2 / 3 - (0.95 + 0.9 + 1.0) / 3))


def test_calibration_of_nothing_is_empty_bins_and_no_error():
    rows = harness.calibration([])
    assert [r["count"] for r in rows] == [0, 0, 0]
    assert harness.calibration_error(rows) is None


def test_report_shows_calibration_and_tokens_in_ascii():
    results = [cal_result(0.95, True), cal_result(0.5, False)]
    results[0].input_tokens, results[0].output_tokens = 100, 10

    text = harness.format_report(harness.aggregate(results))

    assert "Confidence calibration" in text
    assert "[0.85, 1.00]" in text and "<- materiality treats this as high confidence" in text
    assert "expected calibration error" in text
    assert "Extraction tokens: 100 in / 10 out over 1 calls" in text
    text.encode("ascii")
```

In `tests/test_evals_runs.py`, update the expected summary in `test_build_run_record_captures_metadata_summary_and_results` to:

```python
    assert record["summary"] == {
        "total": 2, "passed": 1, "failed": 0, "errored": 1,
        "field_accuracy": {"skip": [1, 1], "entity": [1, 1], "summary": [1, 1]},
        "judge_passed": 1, "judge_total": 1,
        "calibration": [
            {"low": 0.0, "high": 0.6, "count": 0, "mean_confidence": None, "accuracy": None},
            {"low": 0.6, "high": 0.85, "count": 0, "mean_confidence": None, "accuracy": None},
            {"low": 0.85, "high": 1.0, "count": 0, "mean_confidence": None, "accuracy": None},
        ],
        "tokens": {},
    }
```

and append:

```python
def test_compare_runs_accepts_records_without_new_fields():
    old = make_record()
    del old["summary"]["calibration"]
    del old["summary"]["tokens"]
    for result in old["results"]:
        for key in ("confidence", "input_tokens", "output_tokens"):
            result.pop(key, None)

    comparison = runs.compare_runs(old, make_record())

    assert comparison.regressions == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_evals_harness.py tests/test_evals_runs.py -v`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'confidence'`, `AttributeError: ... 'calibration'`).

- [ ] **Step 3: Extend the harness data types and grading**

In `evals/harness.py`, add `import materiality` to the imports. Change the two dataclasses:

```python
@dataclass
class ExampleResult:
    example_id: str
    source: str
    status: str
    fields: list = field(default_factory=list)
    error: str | None = None
    confidence: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class Report:
    total: int
    passed: int
    failed: int
    errored: int
    field_accuracy: dict
    signal_type_confusion: Counter
    judge_passed: int
    judge_total: int
    failures: list
    errors: list
    calibration: list = field(default_factory=list)
    tokens: dict = field(default_factory=dict)
```

Replace `grade_extraction_example` and `_finish`:

```python
def grade_extraction_example(example, client):
    try:
        reply = extract.request_extraction(client, {**example.evidence, "evidence_id": example.id})
    except extract.ExtractionAPIError as e:
        return ExampleResult(example.id, example.source, "errored", error=str(e))
    usage = {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens}
    try:
        actual = extract.parse_extraction_reply(reply, example.id)
    except extract.ExtractionResponseError as e:
        return ExampleResult(example.id, example.source, "failed", error=str(e), **usage)

    extra = {**usage, "confidence": actual["confidence"] if actual else None}
    expected = example.expected
    actual_skip = actual is None
    fields = [_exact("skip", expected["skip"], actual_skip)]
    if expected["skip"] or actual_skip:
        return _finish(example.id, example.source, fields, **extra)

    fields.append(_exact("signal_type", expected["signal_type"], actual["signal_type"]))
    fields.append(FieldResult(
        "entity", "entity", expected["entity"], actual["entity"],
        entity_matches(expected["entity"], actual["entity"]),
    ))
    fields.append(_exact("effective_date", expected["effective_date"], actual["effective_date"]))

    try:
        verdict = judge.judge_summary(example.evidence, example.reference_summary, actual["summary"], client)
    except judge.JudgeError as e:
        return ExampleResult(example.id, example.source, "errored", fields, error=f"judge: {e}", **extra)
    fields.append(FieldResult(
        "summary", "summary", example.reference_summary, actual["summary"], verdict.passed, detail=verdict.reason,
    ))
    return _finish(example.id, example.source, fields, **extra)


def _finish(example_id, source, fields, **extra):
    status = "passed" if all(f.passed for f in fields) else "failed"
    return ExampleResult(example_id, source, status, fields, **extra)
```

- [ ] **Step 4: Add calibration and token totals**

```python
CALIBRATION_BINS = (
    (0.0, materiality.CONFIDENCE_MEDIUM_THRESHOLD),
    (materiality.CONFIDENCE_MEDIUM_THRESHOLD, materiality.CONFIDENCE_HIGH_THRESHOLD),
    (materiality.CONFIDENCE_HIGH_THRESHOLD, 1.0),
)


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def calibration(results, bins=CALIBRATION_BINS):
    """signal_type accuracy per stated-confidence bin, over graded results where signal_type was
    compared. Bin edges are the materiality thresholds, so the last bin is what materiality
    calls "high confidence". The last bin includes its upper edge."""
    points = []
    for result in results:
        if result.status == "errored" or not _is_number(result.confidence):
            continue
        graded = next((f for f in result.fields if f.field == "signal_type"), None)
        if graded is not None:
            points.append((float(result.confidence), graded.passed))

    rows = []
    for i, (low, high) in enumerate(bins):
        last = i == len(bins) - 1
        in_bin = [(c, ok) for c, ok in points if low <= c < high or (last and c == high)]
        rows.append({
            "low": low, "high": high, "count": len(in_bin),
            "mean_confidence": sum(c for c, _ in in_bin) / len(in_bin) if in_bin else None,
            "accuracy": sum(ok for _, ok in in_bin) / len(in_bin) if in_bin else None,
        })
    return rows


def calibration_error(rows):
    """Expected calibration error: count-weighted mean |accuracy - mean confidence| over bins."""
    total = sum(row["count"] for row in rows)
    if not total:
        return None
    return sum(row["count"] / total * abs(row["accuracy"] - row["mean_confidence"]) for row in rows if row["count"])


def _token_totals(results):
    with_usage = [r for r in results if r.input_tokens is not None]
    if not with_usage:
        return {}
    return {"calls": len(with_usage), "input": sum(r.input_tokens for r in with_usage),
            "output": sum(r.output_tokens or 0 for r in with_usage)}
```

In `aggregate`, pass the two new fields to `Report(...)`:

```python
        calibration=calibration(results),
        tokens=_token_totals(results),
```

In `format_report`, insert after the "Summary judge pass rate" block:

```python
    if any(row["count"] for row in report.calibration):
        lines += ["", "Confidence calibration (signal_type accuracy by stated confidence, errored excluded):"]
        for row in report.calibration:
            closing = "]" if row is report.calibration[-1] else ")"
            label = f"[{row['low']:.2f}, {row['high']:.2f}{closing}"
            line = f"  {label:<14} n={row['count']}"
            if row["count"]:
                line += f"  mean confidence {row['mean_confidence']:.2f}  accuracy {100 * row['accuracy']:.1f}%"
            if row["low"] == materiality.CONFIDENCE_HIGH_THRESHOLD:
                line += "  <- materiality treats this as high confidence"
            lines.append(line)
        lines.append(f"  expected calibration error: {calibration_error(report.calibration):.3f}")

    if report.tokens:
        lines += ["", f"Extraction tokens: {report.tokens['input']} in / {report.tokens['output']} out "
                      f"over {report.tokens['calls']} calls"]
```

- [ ] **Step 5: Save calibration and tokens in run records**

In `evals/runs.py`, `build_run_record`, add to the `summary` dict:

```python
            "calibration": report.calibration,
            "tokens": report.tokens,
```

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_evals_harness.py tests/test_evals_runs.py tests/test_evals_runners.py -v`
Expected: PASS.

- [ ] **Step 7: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add evals/harness.py evals/runs.py tests/test_evals_harness.py tests/test_evals_runs.py
git commit -m "feat(evals): report confidence calibration and token usage for extraction runs"
```

---

### Task 2: Property-based fuzzing of every response parser, and the fixes it pins

**Files:**
- Modify: `requirements-dev.txt` (add `hypothesis`)
- Modify: `extract.py` (`parse_extraction_text`)
- Create: `tests/test_response_fuzz.py`
- Test: `tests/test_extract.py` (append)

**Interfaces:**
- Consumes: `extract.parse_extraction_text`, `match.resolve_decisions`, `match.normalize_slug` (P1); `judge.parse_judge_response`.
- Produces: stricter `extract.parse_extraction_text`:
  - `skip` only when `skip is True`;
  - `confidence` must be a finite number in [0, 1] and comes back as `float`;
  - `summary` must be a non-empty string;
  - `effective_date` must be a valid ISO date, otherwise it becomes `None`.

- [ ] **Step 1: Add the dependency**

Append to `requirements-dev.txt`:

```text
hypothesis
```

Run: `pip install -r requirements-dev.txt`

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_extract.py`:

```python
@pytest.mark.parametrize("confidence", ["high", 1.7, -0.1, True, None, float("inf")])
def test_invalid_confidence_is_a_response_error(confidence):
    raw = json.dumps({"signal_type": "new_feature_demand", "summary": "s", "confidence": confidence})
    with pytest.raises(extract.ExtractionResponseError, match="invalid confidence"):
        extract.parse_extraction_text(raw, "x")


def test_integer_confidence_is_returned_as_float():
    raw = json.dumps({"signal_type": "new_feature_demand", "summary": "s", "confidence": 1})
    assert extract.parse_extraction_text(raw, "x")["confidence"] == 1.0


@pytest.mark.parametrize("summary", ["", "   ", 42, None, ["a"]])
def test_empty_or_non_string_summary_is_a_response_error(summary):
    raw = json.dumps({"signal_type": "new_feature_demand", "summary": summary, "confidence": 0.5})
    with pytest.raises(extract.ExtractionResponseError, match="invalid summary"):
        extract.parse_extraction_text(raw, "x")


@pytest.mark.parametrize("skip", ["no", 1, "false", [True]])
def test_only_literal_true_means_skip(skip):
    raw = json.dumps({"skip": skip, "signal_type": "new_feature_demand", "summary": "s", "confidence": 0.5})
    assert extract.parse_extraction_text(raw, "x")["signal_type"] == "new_feature_demand"


@pytest.mark.parametrize("value", ["next spring", "2027-13-01", 20270531, ""])
def test_unusable_effective_date_becomes_none(value):
    raw = json.dumps({"signal_type": "new_feature_demand", "summary": "s", "confidence": 0.5, "effective_date": value})
    assert extract.parse_extraction_text(raw, "x")["effective_date"] is None
```

Create `tests/test_response_fuzz.py`:

```python
"""Property tests: no model output, however malformed, may crash a parser or slip an invalid
value past it. Deterministic (Hypothesis, no API)."""
import json
import re

from hypothesis import given, settings
from hypothesis import strategies as st

import extract
import match
from evals import judge

SLUG_RE = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TOPICS = [{"topic_id": "TOPIC-0001", "slug": "dark-mode", "name": "Dark mode", "description": "d"}]

scalars = (st.none() | st.booleans() | st.integers(-10, 10)
           | st.floats(allow_nan=True, allow_infinity=True) | st.text(max_size=20))
json_values = st.recursive(
    scalars, lambda children: st.lists(children, max_size=4) | st.dictionaries(st.text(max_size=10), children, max_size=4),
    max_leaves=12,
)

near_valid_extraction = st.fixed_dictionaries(
    {
        "signal_type": st.sampled_from(sorted(extract.SIGNAL_TYPES)) | json_values,
        "summary": st.text(max_size=40) | json_values,
        "confidence": st.floats(min_value=-1, max_value=2) | json_values,
    },
    optional={"skip": json_values, "entity": json_values,
              "effective_date": st.sampled_from(["2027-05-31", "2027-02-30", "soon"]) | json_values},
)

decision = st.fixed_dictionaries({}, optional={
    "index": st.integers(-2, 5) | json_values,
    "matched_topic_id": st.sampled_from(["TOPIC-0001", "TOPIC-9999", ""]) | json_values,
    "new_topic": st.fixed_dictionaries({}, optional={
        "name": st.text(max_size=15) | json_values,
        "slug": st.text(max_size=15) | json_values,
        "description": st.text(max_size=15) | json_values,
    }) | json_values,
})


@settings(max_examples=400, deadline=None)
@given(st.one_of(json_values, near_valid_extraction))
def test_extraction_parser_returns_a_valid_result_or_raises_response_error(value):
    raw = json.dumps(value)
    try:
        result = extract.parse_extraction_text(raw, "fuzz")
    except extract.ExtractionResponseError:
        return
    if result is None:
        assert isinstance(value, dict) and value.get("skip") is True
        return
    assert result["signal_type"] in extract.SIGNAL_TYPES
    assert isinstance(result["summary"], str) and result["summary"].strip()
    assert isinstance(result["confidence"], float) and 0.0 <= result["confidence"] <= 1.0
    assert result["entity"] is None or isinstance(result["entity"], dict)
    assert result["effective_date"] is None or DATE_RE.fullmatch(result["effective_date"])


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=200))
def test_extraction_parser_only_raises_response_errors_on_arbitrary_text(text):
    try:
        extract.parse_extraction_text(text, "fuzz")
    except extract.ExtractionResponseError:
        pass


@settings(max_examples=400, deadline=None)
@given(st.one_of(json_values, st.lists(decision | json_values, max_size=6)), st.integers(0, 4))
def test_resolution_is_total_and_only_produces_valid_actions(decisions, n):
    candidates = [{"candidate_id": f"SC-{i}"} for i in range(n)]

    resolutions = match.resolve_decisions(candidates, decisions, TOPICS)

    assert [r.index for r in resolutions] == list(range(n))
    for r in resolutions:
        assert r.action in ("match", "create", "invalid")
        if r.action == "match":
            assert r.topic_id == "TOPIC-0001"
        elif r.action == "create":
            assert SLUG_RE.fullmatch(r.new_topic["slug"])
            assert r.new_topic["name"].strip() and r.new_topic["description"].strip()
            assert r.new_topic["slug"] != "dark-mode"
        else:
            assert r.reason


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=40))
def test_normalize_slug_output_is_always_a_valid_slug_or_empty(text):
    slug = match.normalize_slug(text)
    assert slug == "" or SLUG_RE.fullmatch(slug)


@settings(max_examples=300, deadline=None)
@given(st.one_of(st.text(max_size=200), json_values.map(json.dumps)))
def test_judge_parser_returns_a_verdict_or_raises_response_error(text):
    try:
        verdict = judge.parse_judge_response(text)
    except judge.JudgeResponseError:
        return
    assert isinstance(verdict.passed, bool) and isinstance(verdict.reason, str)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `pytest tests/test_extract.py tests/test_response_fuzz.py -v`
Expected: the new `test_extract.py` tests FAIL, and `test_extraction_parser_returns_a_valid_result_or_raises_response_error` FAILS with a falsifying example (for instance `confidence: "high"` or `skip: "no"`).

- [ ] **Step 4: Tighten `parse_extraction_text`**

In `extract.py`, add `import datetime` and `import math` to the imports, and replace `parse_extraction_text` with:

```python
def parse_extraction_text(raw_text, evidence_label):
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as e:
        raise ExtractionResponseError(f"evidence {evidence_label}: non-JSON response: {raw_text!r}") from e

    if not isinstance(parsed, dict):
        raise ExtractionResponseError(f"evidence {evidence_label}: expected a JSON object, got {raw_text!r}")

    if parsed.get("skip") is True:
        return None

    missing = {"signal_type", "summary", "confidence"} - parsed.keys()
    if missing:
        raise ExtractionResponseError(f"evidence {evidence_label}: response missing keys {missing}")

    if not isinstance(parsed["signal_type"], str) or parsed["signal_type"] not in SIGNAL_TYPES:
        raise ExtractionResponseError(f"evidence {evidence_label}: invalid signal_type {parsed['signal_type']!r}")

    summary = parsed["summary"]
    if not isinstance(summary, str) or not summary.strip():
        raise ExtractionResponseError(f"evidence {evidence_label}: invalid summary {summary!r}")

    confidence = parsed["confidence"]
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0):
        raise ExtractionResponseError(f"evidence {evidence_label}: invalid confidence {confidence!r}")

    entity = parsed.get("entity")
    return {
        "signal_type": parsed["signal_type"],
        "summary": summary,
        "confidence": float(confidence),
        "entity": entity if isinstance(entity, dict) else None,
        "effective_date": _iso_date_or_none(parsed.get("effective_date")),
    }


_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _iso_date_or_none(value):
    # Python 3.11's date.fromisoformat also accepts "20270531" and week dates like "2027-W22-1",
    # so require the exact YYYY-MM-DD shape first, then check that it is a real calendar date.
    if not isinstance(value, str) or not _ISO_DATE_RE.fullmatch(value):
        return None
    try:
        datetime.date.fromisoformat(value)
    except ValueError:
        return None
    return value
```

Also add `import re` to the imports of `extract.py`.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_extract.py tests/test_response_fuzz.py tests/test_evals_harness.py -v`
Expected: PASS. If Hypothesis finds a counterexample in `resolve_decisions` or `parse_judge_response`, fix the parser (never the property) and add the counterexample as a regular parametrized test next to the existing ones.

- [ ] **Step 6: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add requirements-dev.txt extract.py tests/test_extract.py tests/test_response_fuzz.py
git commit -m "test: property-based fuzzing of all response parsers; validate confidence, summary, skip and dates"
```

---

### Task 3: Metamorphic checks for matching

**Files:**
- Create: `evals/oracles.py`
- Test: `tests/test_evals_oracles.py` (create)

**Interfaces:**
- Consumes: `match.call_matcher`, `match.resolve_decisions`, `match.normalize_slug`, `match.MatchAPIError`, `match.MatchResponseError`; `harness.MatchingScenario`, `ExampleResult`, `FieldResult`.
- Produces:
  - `oracles.Placement(assignments: tuple, new_groups: frozenset, slugs: tuple, new_topics: dict)` (equality compares only `assignments` and `new_groups`)
  - `oracles.placement(candidates, decisions, existing_topics) -> Placement`
  - `oracles.unshuffle(p, order) -> Placement`
  - `oracles.describe(p) -> str`
  - `oracles.check_matching_scenario(scenario, client, rng) -> ExampleResult`, with fields `identity`, `order` (when the shuffle moved something) and `self_match` (when the first run proposed new topics). `rng` only needs a `.shuffle(list)` method.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_evals_oracles.py`:

```python
import json
import re

from evals import harness, oracles

CANDIDATE_RE = re.compile(r'"index": (\d+),\s*"signal_type": "[^"]*",\s*"summary": "([^"]*)"')
TOPIC_RE = re.compile(r'"topic_id": "([^"]+)",\s*"name": "([^"]+)"')


class Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class Response:
    stop_reason = "end_turn"

    def __init__(self, text):
        self.content = [Text(text)]


class ScriptedMatcher:
    """A matcher that decides by summary, never by position: ("topic", id) or ("new", name)."""

    def __init__(self, by_summary, positional=False):
        self.by_summary, self.positional, self.calls = by_summary, positional, 0

    def create(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0]["content"]
        registry = {name: topic_id for topic_id, name in TOPIC_RE.findall(prompt)}
        decisions = []
        for index, summary in CANDIDATE_RE.findall(prompt):
            kind, value = self.by_summary[summary] if not self.positional else list(self.by_summary.values())[int(index)]
            if kind == "topic":
                decisions.append({"index": int(index), "matched_topic_id": value})
            elif value in registry:
                decisions.append({"index": int(index), "matched_topic_id": registry[value]})
            else:
                decisions.append({"index": int(index), "matched_topic_id": None,
                                  "new_topic": {"name": value, "slug": value.lower().replace(" ", "-"), "description": value}})
        return Response(json.dumps(decisions))


class Client:
    def __init__(self, messages):
        self.messages = messages


class Reverse:
    def shuffle(self, items):
        items.reverse()


SCENARIO = harness.MatchingScenario(
    name="mixed", source="mixed",
    existing_topics=[{"topic_id": "TOPIC-0001", "name": "Dark mode", "description": "d"}],
    candidates=[{"signal_type": "new_feature_demand", "summary": "dark theme please"},
                {"signal_type": "new_feature_demand", "summary": "csv export"},
                {"signal_type": "new_feature_demand", "summary": "export to csv"}],
    expected=[],
)
CONSISTENT = {"dark theme please": ("topic", "TOPIC-0001"), "csv export": ("new", "CSV export"),
              "export to csv": ("new", "CSV export")}


def test_placement_groups_new_topics_by_slug_and_ignores_labels_in_equality():
    decisions = [{"index": 0, "matched_topic_id": "TOPIC-0001"},
                 {"index": 1, "new_topic": {"name": "CSV", "slug": "csv", "description": "d"}},
                 {"index": 2, "new_topic": {"name": "CSV export", "slug": "csv", "description": "d"}}]
    renamed = [decisions[0],
               {"index": 1, "new_topic": {"name": "Exports", "slug": "exports", "description": "d"}},
               {"index": 2, "new_topic": {"name": "Exports", "slug": "exports", "description": "d"}}]

    a = oracles.placement(SCENARIO.candidates, decisions, SCENARIO.existing_topics)
    b = oracles.placement(SCENARIO.candidates, renamed, SCENARIO.existing_topics)

    assert a.assignments == ("TOPIC-0001", "NEW", "NEW")
    assert a.new_groups == frozenset({frozenset({1, 2})})
    assert a == b


def test_unshuffle_maps_positions_back():
    shuffled = oracles.Placement(("NEW", "NEW", "TOPIC-0001"), frozenset({frozenset({0, 1})}), ("csv", "csv", None))
    restored = oracles.unshuffle(shuffled, [2, 1, 0])
    assert restored.assignments == ("TOPIC-0001", "NEW", "NEW")
    assert restored.new_groups == frozenset({frozenset({1, 2})})
    assert restored.slugs == (None, "csv", "csv")


def test_consistent_matcher_passes_every_check():
    messages = ScriptedMatcher(CONSISTENT)

    result = oracles.check_matching_scenario(SCENARIO, Client(messages), Reverse())

    assert result.status == "passed"
    assert [f.field for f in result.fields] == ["identity", "order", "self_match"]
    assert messages.calls == 4


def test_position_dependent_matcher_fails_the_order_check():
    result = oracles.check_matching_scenario(SCENARIO, Client(ScriptedMatcher(CONSISTENT, positional=True)), Reverse())

    by_field = {f.field: f.passed for f in result.fields}
    assert by_field["identity"] is True
    assert by_field["order"] is False


def test_identity_noise_is_reported_separately():
    class Flaky(ScriptedMatcher):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            if self.calls == 2:
                decisions = json.loads(response.content[0].text)
                decisions[0] = {"index": 0, "matched_topic_id": None,
                                "new_topic": {"name": "Night", "slug": "night", "description": "d"}}
                return Response(json.dumps(decisions))
            return response

    result = oracles.check_matching_scenario(SCENARIO, Client(Flaky(CONSISTENT)), Reverse())

    assert {f.field: f.passed for f in result.fields}["identity"] is False


def test_api_error_marks_the_scenario_errored():
    class Down:
        def create(self, **kwargs):
            raise RuntimeError("529")

    result = oracles.check_matching_scenario(SCENARIO, Client(Down()), Reverse())
    assert result.status == "errored"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_evals_oracles.py -v`
Expected: FAIL (`ImportError: cannot import name 'oracles'`).

- [ ] **Step 3: Create `evals/oracles.py`**

```python
"""Label-free checks ("oracles") for the matching and extraction stages.

Golden sets need a human label for every example. These checks need none: they ask the model the
same question in ways that must not change the answer, and count how often the answer changes.

Matching, per scenario (4 calls):
- identity: the same input twice. This is the model's own run-to-run noise; read the other two
  checks against it. If identity fails often, order and self_match failures say little.
- order: the candidates shuffled. Where a candidate lands must not depend on its position.
- self_match: every topic the first run proposed is added to the registry and the same
  candidates are sent again. Each must now match the topic it created.

Extraction, per example (1 + N calls): N cached paraphrases of the post must get the same skip
decision and signal_type as the original did (see evals/paraphrase.py).
"""
from dataclasses import dataclass, field

import extract
import match
from evals.harness import ExampleResult, FieldResult


@dataclass(frozen=True)
class Placement:
    """Where each candidate landed: an existing topic id, "NEW", or "INVALID", plus which
    candidates share a new topic. Equality ignores new-topic names and slugs: two runs that group
    the same candidates under differently worded new topics agree."""
    assignments: tuple
    new_groups: frozenset
    slugs: tuple = field(default=(), compare=False)
    new_topics: dict = field(default_factory=dict, compare=False, hash=False)


def placement(candidates, decisions, existing_topics):
    resolutions = match.resolve_decisions(candidates, decisions, existing_topics)
    assignments, slugs, groups, topics = [], [], {}, {}
    for r in resolutions:
        if r.action == "create":
            slug = r.new_topic["slug"]
            assignments.append("NEW")
            slugs.append(slug)
            groups.setdefault(slug, set()).add(r.index)
            topics.setdefault(slug, r.new_topic)
        else:
            assignments.append(r.topic_id if r.action == "match" else "INVALID")
            slugs.append(None)
    return Placement(tuple(assignments), frozenset(frozenset(g) for g in groups.values()), tuple(slugs), topics)


def unshuffle(p, order):
    """`p` was computed on candidates reordered so that position j held original candidate order[j]."""
    assignments, slugs = [None] * len(order), [None] * len(order)
    for j, original in enumerate(order):
        assignments[original] = p.assignments[j]
        slugs[original] = p.slugs[j] if p.slugs else None
    groups = frozenset(frozenset(order[j] for j in group) for group in p.new_groups)
    return Placement(tuple(assignments), groups, tuple(slugs), p.new_topics)


def describe(p):
    return f"{list(p.assignments)} new groups {sorted(sorted(g) for g in p.new_groups)}"


def _place(client, candidates, existing):
    return placement(candidates, match.call_matcher(client, candidates, existing), existing)


def _compare(check, expected, actual):
    return FieldResult(check, check, describe(expected), describe(actual), expected == actual)


def _status(fields):
    return "passed" if all(f.passed for f in fields) else "failed"


def check_matching_scenario(scenario, client, rng):
    candidates, existing = scenario.candidates, scenario.existing_topics
    try:
        base = _place(client, candidates, existing)
        fields = [_compare("identity", base, _place(client, candidates, existing))]

        order = list(range(len(candidates)))
        rng.shuffle(order)
        if order != sorted(order):
            shuffled = [candidates[i] for i in order]
            fields.append(_compare("order", base, unshuffle(_place(client, shuffled, existing), order)))

        if base.new_topics:
            oracle_ids = {slug: f"TOPIC-ORACLE-{k}" for k, slug in enumerate(sorted(base.new_topics), 1)}
            registry = [*existing, *({"topic_id": oracle_ids[slug], "slug": slug, "name": topic["name"],
                                      "description": topic["description"]} for slug, topic in base.new_topics.items())]
            expected = tuple(oracle_ids[base.slugs[i]] if a == "NEW" else a for i, a in enumerate(base.assignments))
            after = _place(client, candidates, registry)
            fields.append(FieldResult("self_match", "self_match", str(list(expected)), str(list(after.assignments)),
                                      after.assignments == expected))
    except match.MatchAPIError as e:
        return ExampleResult(scenario.name, scenario.source, "errored", error=str(e))
    except match.MatchResponseError as e:
        return ExampleResult(scenario.name, scenario.source, "failed", error=str(e))
    return ExampleResult(scenario.name, scenario.source, _status(fields), fields)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_evals_oracles.py -v`
Expected: PASS.

- [ ] **Step 5: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add evals/oracles.py tests/test_evals_oracles.py
git commit -m "feat(evals): identity, order and self-match oracles for topic matching"
```

---

### Task 4: Paraphrase cache and the extraction stability check

**Files:**
- Create: `evals/paraphrase.py`
- Modify: `evals/oracles.py` (add `check_extraction_stability`)
- Test: `tests/test_evals_oracles.py` (append), `tests/test_evals_golden.py` (append)

**Interfaces:**
- Consumes: `llm.reply_from_response`, `fingerprints.fingerprint` (P1); `extract.extract_topic`; `harness.GoldenSchemaError`, `ExtractionExample`.
- Produces:
  - `paraphrase.PARAPHRASE_MODEL = "claude-sonnet-5"`, `paraphrase.PARAPHRASE_PROMPT_TEMPLATE`, `paraphrase.PARAPHRASE_DIR: Path`
  - `paraphrase.ParaphraseError(Exception)`
  - `paraphrase.generate_paraphrases(client, evidence, n) -> list[dict]` (each `{"title", "content"}`)
  - `paraphrase.load_paraphrases(source, directory=PARAPHRASE_DIR) -> dict[str, list[dict]]`
  - `paraphrase.save_paraphrases(source, examples, directory=PARAPHRASE_DIR) -> Path`
  - `oracles.check_extraction_stability(example, paraphrases, client) -> ExampleResult`, with one `stability` field per paraphrase, labeled `paraphrase[k]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evals_oracles.py`:

```python
import pytest

from evals import paraphrase

EXAMPLE = harness.ExtractionExample(
    id="db-001", source="database_development",
    evidence={"title": "Migration tool times out", "content": "It hangs on 40M-row tables."},
    expected={"skip": False, "signal_type": "reliability_issue", "entity": None, "effective_date": None},
    reference_summary="r",
)


class ByTitle:
    def __init__(self, answers):
        self.answers, self.calls = answers, 0

    def create(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0]["content"]
        title = next(t for t in self.answers if t in prompt)
        return Response(self.answers[title])


def extraction(signal_type):
    return json.dumps({"signal_type": signal_type, "summary": "s", "confidence": 0.9})


def test_stable_extractor_passes_and_unstable_paraphrase_fails():
    paraphrases = [{"title": "Migrations hang", "content": "big tables"}, {"title": "Is there dark mode", "content": "?"}]
    client = Client(ByTitle({"Migration tool times out": extraction("reliability_issue"),
                             "Migrations hang": extraction("reliability_issue"),
                             "Is there dark mode": json.dumps({"skip": True})}))

    result = oracles.check_extraction_stability(EXAMPLE, paraphrases, client)

    assert result.status == "failed"
    assert [(f.label, f.expected, f.actual, f.passed) for f in result.fields] == [
        ("paraphrase[0]", "reliability_issue", "reliability_issue", True),
        ("paraphrase[1]", "reliability_issue", "SKIP", False),
    ]


def test_unusable_paraphrase_response_fails_only_that_field():
    client = Client(ByTitle({"Migration tool times out": extraction("reliability_issue"), "P1": "not json"}))
    result = oracles.check_extraction_stability(EXAMPLE, [{"title": "P1", "content": "c"}], client)
    assert result.fields[0].passed is False
    assert result.fields[0].actual.startswith("unusable")


def test_generate_paraphrases_parses_and_trims():
    class One:
        def create(self, **kwargs):
            assert kwargs["model"] == paraphrase.PARAPHRASE_MODEL
            return Response(json.dumps([{"title": " A ", "content": "x"}, {"title": "", "content": "y"},
                                        "junk", {"title": "B", "content": "z"}, {"title": "C", "content": "w"}]))

    assert paraphrase.generate_paraphrases(Client(One()), EXAMPLE.evidence, 2) == [
        {"title": "A", "content": "x"}, {"title": "B", "content": "z"}]


def test_generate_paraphrases_rejects_unusable_output():
    class Bad:
        def create(self, **kwargs):
            return Response("[]")

    with pytest.raises(paraphrase.ParaphraseError):
        paraphrase.generate_paraphrases(Client(Bad()), EXAMPLE.evidence, 2)


def test_paraphrase_cache_round_trips_and_validates(tmp_path):
    examples = {"db-001": [{"title": "t", "content": "c"}]}
    path = paraphrase.save_paraphrases("database_development", examples, tmp_path)

    assert path.read_text(encoding="utf-8").startswith("# Paraphrases")
    assert paraphrase.load_paraphrases("database_development", tmp_path) == examples
    assert paraphrase.load_paraphrases("missing", tmp_path) == {}

    path.write_text("examples:\n  db-001:\n    - title: 1\n", encoding="utf-8")
    with pytest.raises(harness.GoldenSchemaError, match="db-001"):
        paraphrase.load_paraphrases("database_development", tmp_path)
```

Append to `tests/test_evals_golden.py`:

```python
def test_committed_paraphrase_files_are_valid():
    from evals import paraphrase
    if not paraphrase.PARAPHRASE_DIR.exists():
        return
    for path in paraphrase.PARAPHRASE_DIR.glob("*.yaml"):
        paraphrase.load_paraphrases(path.stem)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_evals_oracles.py tests/test_evals_golden.py -v`
Expected: FAIL (`ImportError: cannot import name 'paraphrase'`).

- [ ] **Step 3: Create `evals/paraphrase.py`**

```python
"""Generate and cache meaning-preserving paraphrases of golden extraction posts.

The stability oracle (evals/oracles.py) needs the same post worded differently. Paraphrases are
generated once with --generate-missing, written to evals/golden/paraphrases/<source>.yaml and
committed after a human skim, so later runs reuse identical inputs and stay comparable.
"""
import json
from pathlib import Path

import yaml

from evals.harness import GoldenSchemaError
from fingerprints import fingerprint
from llm import reply_from_response

PARAPHRASE_MODEL = "claude-sonnet-5"
PARAPHRASE_DIR = Path(__file__).resolve().parent / "golden" / "paraphrases"

PARAPHRASE_PROMPT_TEMPLATE = """Rewrite the Reddit post below {n} different ways.

Keep exactly the same meaning: the same product, the same problem or request, the same facts, \
numbers, dates and sentiment, and the same intent. If the author says they are switching \
products, every rewrite says so. If the post is spam or off-topic, every rewrite is too. Change \
only the wording: vary the length, the tone (casual, terse, frustrated, formal) and the sentence \
order, and add or remove small typos.

Post title: {title}
Post body: {content}

Respond with ONLY a JSON array of {n} objects, each {{"title": "...", "content": "..."}}.
"""

HEADER = """\
# Paraphrases for the extraction stability oracle (python evals/run_oracle_eval.py --kind extraction).
# Generated by a model. Skim every one and delete any that changes the meaning, the product, a fact
# or the intent: a paraphrase that changes the meaning makes a correct extractor look unstable.
"""


class ParaphraseError(Exception):
    pass


def generate_paraphrases(client, evidence, n):
    prompt = PARAPHRASE_PROMPT_TEMPLATE.format(n=n, title=evidence["title"], content=evidence["content"])
    try:
        response = client.messages.create(model=PARAPHRASE_MODEL, max_tokens=8000,
                                          messages=[{"role": "user", "content": prompt}])
    except Exception as e:
        raise ParaphraseError(f"paraphrase call failed: {e}") from e
    reply = reply_from_response(response)
    if reply.text is None:
        raise ParaphraseError(f"paraphrase response has no text block (stop_reason={reply.stop_reason!r})")
    try:
        items = json.loads(reply.text)
    except json.JSONDecodeError as e:
        raise ParaphraseError(f"paraphrase response is not JSON: {reply.text!r}") from e
    usable = [
        {"title": item["title"].strip(), "content": item["content"].strip()}
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and isinstance(item.get("title"), str) and item["title"].strip()
        and isinstance(item.get("content"), str)
    ]
    if not usable:
        raise ParaphraseError("paraphrase response had no usable paraphrases")
    return usable[:n]


def load_paraphrases(source, directory=PARAPHRASE_DIR):
    path = Path(directory) / f"{source}.yaml"
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    examples = data.get("examples")
    if not isinstance(examples, dict):
        raise GoldenSchemaError(f"{path.name}: 'examples' must map example ids to lists of paraphrases")
    for example_id, items in examples.items():
        if not isinstance(items, list) or not all(
            isinstance(i, dict) and isinstance(i.get("title"), str) and isinstance(i.get("content"), str) for i in items
        ):
            raise GoldenSchemaError(f"{path.name} [{example_id}]: each paraphrase needs a string title and content")
    return examples


def save_paraphrases(source, examples, directory=PARAPHRASE_DIR):
    path = Path(directory) / f"{source}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"source": source,
            "generator": {"model": PARAPHRASE_MODEL, "prompt": fingerprint(PARAPHRASE_PROMPT_TEMPLATE)},
            "examples": examples}
    path.write_text(HEADER + yaml.safe_dump(body, sort_keys=True, allow_unicode=True, width=100), encoding="utf-8")
    return path
```

- [ ] **Step 4: Add the stability check to `evals/oracles.py`**

```python
def _label(client, evidence):
    result = extract.extract_topic(client, evidence)
    return "SKIP" if result is None else result["signal_type"]


def check_extraction_stability(example, paraphrases, client):
    try:
        original = _label(client, {**example.evidence, "evidence_id": example.id})
    except extract.ExtractionAPIError as e:
        return ExampleResult(example.id, example.source, "errored", error=str(e))
    except extract.ExtractionResponseError as e:
        return ExampleResult(example.id, example.source, "failed", error=f"original: {e}")

    fields = []
    for k, item in enumerate(paraphrases):
        label = f"paraphrase[{k}]"
        try:
            got = _label(client, {**item, "evidence_id": f"{example.id}~{k}"})
        except extract.ExtractionAPIError as e:
            return ExampleResult(example.id, example.source, "errored", fields, error=str(e))
        except extract.ExtractionResponseError as e:
            fields.append(FieldResult("stability", label, original, f"unusable: {e}", False))
            continue
        fields.append(FieldResult("stability", label, original, got, got == original))
    return ExampleResult(example.id, example.source, _status(fields), fields)
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_evals_oracles.py tests/test_evals_golden.py -v`
Expected: PASS.

- [ ] **Step 6: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add evals/paraphrase.py evals/oracles.py tests/test_evals_oracles.py tests/test_evals_golden.py
git commit -m "feat(evals): cached paraphrases and a label-free extraction stability check"
```

---

### Task 5: Oracle runner, run records, docs

**Files:**
- Create: `evals/run_oracle_eval.py`
- Modify: `evals/runs.py` (`KINDS`)
- Modify: `README.md` (Evals)
- Test: `tests/test_evals_runners.py` (append)

**Interfaces:**
- Consumes: Tasks 1-4; `harness.load_golden_dir`, `filter_examples`, `aggregate`, `format_report`; `runs.build_run_record`, `save_and_announce`, `golden_fingerprint`.
- Produces:
  - `runs.KINDS = ("extraction", "matching", "judge", "oracle_matching", "oracle_extraction")`
  - `run_oracle_eval.main(argv=None, client=None) -> int`, with arguments `--kind {matching,extraction}`, `--filter`, `--seed` (default 0), `--paraphrases` (default 3), `--generate-missing`, `--paraphrase-dir`, `--no-save`, `--results-dir`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evals_runners.py`:

```python
from evals import paraphrase, run_oracle_eval


class Routing:
    """Answers paraphrase prompts with two paraphrases and everything else with one extraction."""

    def create(self, **kwargs):
        prompt = kwargs["messages"][0]["content"]
        if prompt.startswith("Rewrite the Reddit post"):
            return FakeResponse(json.dumps([{"title": "p1", "content": "c"}, {"title": "p2", "content": "c"}]))
        return FakeResponse(json.dumps({"signal_type": "reliability_issue", "summary": "s", "confidence": 0.9}))


class RoutingClient:
    def __init__(self):
        self.messages = Routing()


def test_oracle_extraction_without_cached_paraphrases_exits_2_and_spends_nothing(tmp_path, capsys):
    client = RepeatingClient(RuntimeError("must not be called"))

    code = run_oracle_eval.main(["--kind", "extraction", "--filter", "database_development",
                                 "--paraphrase-dir", str(tmp_path / "p"), "--results-dir", str(tmp_path)], client=client)

    assert code == 2
    assert "--generate-missing" in capsys.readouterr().err


def test_oracle_extraction_generates_missing_paraphrases_then_saves_a_run(tmp_path):
    pdir = tmp_path / "p"

    code = run_oracle_eval.main(["--kind", "extraction", "--filter", "database_development", "--paraphrases", "2",
                                 "--generate-missing", "--paraphrase-dir", str(pdir), "--results-dir", str(tmp_path)],
                                client=RoutingClient())

    assert code == 0
    cached = paraphrase.load_paraphrases("database_development", pdir)
    assert cached and all(len(v) == 2 for v in cached.values())
    [(path, record)], _ = runs.find_runs(tmp_path, "oracle_extraction")
    assert record["models"]["paraphrase"] == paraphrase.PARAPHRASE_MODEL
    assert set(record["prompts"]) == {"extraction", "paraphrase"}
    assert record["summary"]["field_accuracy"]["stability"][1] > 0


def test_oracle_matching_runs_each_scenario_and_saves(tmp_path):
    client = RepeatingClient(MATCH_OK)

    code = run_oracle_eval.main(["--kind", "matching", *POOLING, "--results-dir", str(tmp_path)], client=client)

    assert code == 0
    [(path, record)], _ = runs.find_runs(tmp_path, "oracle_matching")
    assert record["summary"]["field_accuracy"]["identity"] == [1, 1]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_evals_runners.py -v`
Expected: FAIL (`ImportError: cannot import name 'run_oracle_eval'`).

- [ ] **Step 3: Extend `runs.KINDS`**

```python
KINDS = ("extraction", "matching", "judge", "oracle_matching", "oracle_extraction")
```

- [ ] **Step 4: Create `evals/run_oracle_eval.py`**

```python
"""Run the label-free oracle checks against the real API.

Usage:
  python evals/run_oracle_eval.py --kind matching [--filter SCENARIO] [--seed 0]
  python evals/run_oracle_eval.py --kind extraction [--filter FILE] [--paraphrases 3] [--generate-missing]

Matching: 4 calls per scenario (identity, order, self-match). Extraction: 1 + N calls per example
over cached paraphrases; --generate-missing first makes one paraphrase call per example that has
none cached and writes them to evals/golden/paraphrases/ (skim them before committing). Each run
is saved under evals/results/ as kind oracle_matching or oracle_extraction. Not part of CI.
"""
import argparse
import datetime
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import extract  # noqa: E402
import match  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, oracles, paraphrase, runs  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Label-free oracle checks for matching and extraction.")
    parser.add_argument("--kind", choices=("matching", "extraction"), required=True)
    parser.add_argument("--filter", help="matching: scenario name; extraction: golden file basename")
    parser.add_argument("--seed", type=int, default=0, help="matching: shuffle seed")
    parser.add_argument("--paraphrases", type=int, default=3, help="extraction: paraphrases per example")
    parser.add_argument("--generate-missing", action="store_true",
                        help="extraction: generate and cache paraphrases for examples that have none (costs API calls)")
    parser.add_argument("--paraphrase-dir", default=str(paraphrase.PARAPHRASE_DIR))
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR))
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        items = harness.load_golden_dir(os.path.join(GOLDEN, args.kind), args.kind)
    except harness.GoldenSchemaError as e:
        print(f"golden set is malformed: {e}", file=sys.stderr)
        return 2
    selected = harness.filter_examples(items, args.filter)
    if not selected:
        print(f"nothing in the {args.kind} golden set matches --filter {args.filter!r}", file=sys.stderr)
        return 2

    cached = {}
    if args.kind == "extraction":
        try:
            for source in sorted({e.source for e in selected}):
                cached[source] = paraphrase.load_paraphrases(source, args.paraphrase_dir)
        except harness.GoldenSchemaError as e:
            print(f"paraphrase cache is malformed: {e}", file=sys.stderr)
            return 2
        missing = [e for e in selected if not cached[e.source].get(e.id)]
        if missing and not args.generate_missing:
            if len(missing) == len(selected):
                print(f"no cached paraphrases in {args.paraphrase_dir}; rerun with --generate-missing", file=sys.stderr)
                return 2
            print(f"warning: {len(missing)} example(s) have no cached paraphrases and are skipped "
                  f"(--generate-missing would create them)", file=sys.stderr)
            selected = [e for e in selected if e not in missing]
            missing = []

    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    results = []
    if args.kind == "matching":
        models = {"matching": match.MATCH_MODEL}
        prompts = {"matching": runs.fingerprint(match.MATCHING_PROMPT_TEMPLATE)}
        golden = runs.golden_fingerprint(selected)
        for number, scenario in enumerate(selected, 1):
            result = oracles.check_matching_scenario(scenario, client, random.Random(args.seed + number))
            print(f"[{number}/{len(selected)}] {scenario.name}: {result.status}")
            results.append(result)
    else:
        for example in missing:
            try:
                cached[example.source][example.id] = paraphrase.generate_paraphrases(client, example.evidence, args.paraphrases)
            except paraphrase.ParaphraseError as e:
                print(f"warning: could not paraphrase {example.source}/{example.id}: {e}", file=sys.stderr)
        for source in sorted({e.source for e in missing}):
            print(f"wrote paraphrases to {paraphrase.save_paraphrases(source, cached[source], args.paraphrase_dir)}")

        models = {"extraction": extract.EXTRACTION_MODEL, "paraphrase": paraphrase.PARAPHRASE_MODEL}
        prompts = {"extraction": runs.fingerprint(extract.EXTRACTION_PROMPT_TEMPLATE),
                   "paraphrase": runs.fingerprint(paraphrase.PARAPHRASE_PROMPT_TEMPLATE)}
        used = {}
        runnable = [e for e in selected if cached[e.source].get(e.id)]
        for number, example in enumerate(runnable, 1):
            items_for_example = cached[example.source][example.id][:args.paraphrases]
            used[f"{example.source}/{example.id}"] = items_for_example
            result = oracles.check_extraction_stability(example, items_for_example, client)
            print(f"[{number}/{len(runnable)}] {example.source}/{example.id}: {result.status}")
            results.append(result)
        golden = runs.fingerprint([runs.golden_fingerprint(runnable), used])

    report = harness.aggregate(results)
    print()
    print(harness.format_report(report))

    if not args.no_save:
        record = runs.build_run_record(
            f"oracle_{args.kind}", results, report, models=models, prompts=prompts, golden=golden,
            filter_name=args.filter, started_at=started_at, commit=runs.git_commit(),
        )
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_evals_runners.py tests/test_evals_runs.py -v`
Expected: PASS.

- [ ] **Step 6: Document it in `README.md`**

Append to the **Evals** section:

````markdown
### Label-free checks

These need no human labels, so they scale past the golden sets.

- **Confidence calibration.** Every extraction run prints signal_type accuracy per stated-confidence bin. The bins are cut at the materiality thresholds (0.6, 0.85). The last bin is what materiality treats as "high confidence"; if its accuracy is far below its mean confidence, the materiality rule is trusting numbers that aren't true. With a few dozen examples the bins are noisy: read the counts.
- **Matching oracles.**

  ```powershell
  python evals/run_oracle_eval.py --kind matching [--filter NAME] [--seed 0]
  ```

  4 calls per scenario:
  - `identity`: the same input twice; the model's own noise;
  - `order`: candidates shuffled;
  - `self_match`: proposed topics added to the registry; each candidate must now match the topic it created.

  Read `order` and `self_match` against `identity`.
- **Extraction stability.**

  ```powershell
  python evals/run_oracle_eval.py --kind extraction [--filter FILE] --generate-missing
  ```

  The first run generates paraphrases into `evals/golden/paraphrases/`. Skim them, delete any that change the meaning, and commit them. Later runs reuse the cache (drop `--generate-missing`). A paraphrase must get the same skip decision and signal_type as the original.
- Oracle runs save as `oracle_matching` / `oracle_extraction`. Compare them with `python evals/compare_runs.py --kind oracle_matching`.
- **Parser fuzzing.** `tests/test_response_fuzz.py` (Hypothesis, in CI) feeds random and near-valid model output to every parser. No output may crash a parser or slip an invalid value through.
````

- [ ] **Step 7: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add evals/run_oracle_eval.py evals/runs.py README.md tests/test_evals_runners.py
git commit -m "feat(evals): oracle runner for matching and extraction, saved as comparable runs"
```

- [ ] **Step 8: First real run (manual, costs API calls)**

```powershell
python evals/run_oracle_eval.py --kind matching
python evals/run_oracle_eval.py --kind extraction --generate-missing --paraphrases 3
python evals/run_extraction_eval.py
```

Skim and prune `evals/golden/paraphrases/*.yaml`, then commit them in a separate commit (`chore(evals): reviewed paraphrase cache`). Put three numbers in the PR description:

- the identity, order and self-match rates;
- the stability rate;
- the calibration table.

They are the baselines P4 and P5 compare against.

---

## Self-review notes

- **Spec coverage:**
  - M2 (calibration): Task 1. M1 (label-free measurement): Tasks 3-5. S4 (parser validation): Task 2.
  - Spec P3 decision 2 (cached paraphrases, compared with the model's own original answer): Task 4.
- **Interfaces used by P4:** `ExampleResult.confidence`, `input_tokens`, `output_tokens`; run-record `summary.tokens`; `harness.grade_extraction_example` calls `extract.request_extraction`, so P4's effort setting flows into evals automatically.
- **Type consistency:**
  - `Placement` fields are used the same way in `placement`, `unshuffle` and `check_matching_scenario`.
  - `check_matching_scenario` takes any `rng` with `.shuffle`: the runner passes `random.Random`, the tests pass `Reverse`.
