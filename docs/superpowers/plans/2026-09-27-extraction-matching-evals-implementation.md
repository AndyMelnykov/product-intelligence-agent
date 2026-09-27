# Extraction & Matching Evals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a golden-set eval suite (`evals/`) that runs the real extraction and matching
prompts against the real Anthropic API and grades the output against human-verified labels,
with an LLM judge for the free-text extraction summary.

**Architecture:** `evals/harness.py` holds all deterministic logic (golden YAML loading and
schema validation, per-example grading, aggregation, report formatting) and is unit tested in
`tests/` with the repo's `FakeClient` pattern. `evals/judge.py` makes one Claude call per
extraction example to grade the summary. Two thin CLI scripts (`evals/run_extraction_eval.py`,
`evals/run_matching_eval.py`) wire a real `anthropic.Anthropic` client into the harness and
print a console report. The runners call the production functions `extract.extract_topic` and
`match.call_matcher` directly; they never reimplement prompts or parsing.

**Tech Stack:** Python 3.11+ (CI runs 3.11), Anthropic Python SDK (`anthropic` 0.122),
PyYAML, pytest, ruff.

**Spec:** [docs/superpowers/specs/2026-09-24-extraction-matching-evals-design.md](../specs/2026-09-24-extraction-matching-evals-design.md)

## Global Constraints

- Evals are **not a CI gate**. Nothing in `evals/` may be named `test_*.py`, and no pytest test may make a real API call. Only the deterministic harness/judge tests in `tests/` run in CI.
- Runners call the real production functions (`extract.extract_topic`, `match.call_matcher`, which builds its prompt via `match.build_matching_prompt`). No copies of prompt text in `evals/`.
- The real client is obtained exactly like production: `anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))`.
- `extract.ExtractionAPIError` / `match.MatchAPIError` / `judge.JudgeError` mean the example is `errored`, not graded. `extract.ExtractionResponseError` / `match.MatchResponseError` mean it is `failed` (a model mistake).
- Malformed golden YAML fails fast at load time, before any API call, with the file name and example id in the message, and the runner exits non-zero (exit code 2). Grading outcomes never change the exit code (exit 0).
- v1 output is console-only. No results persistence.
- Judge model: `claude-opus-5` (per the Claude API reference, the default most-capable model; `claude-opus-5-5` is to be used only when the user names it). Opus 5 runs adaptive thinking by default, so response text is read from the first block with `type == "text"`, never `content[0]`.
- Runner console output must not crash on the Windows console (cp1252): runners call `sys.stdout.reconfigure(errors="backslashreplace")` before printing model text.
- Lint: `ruff check .` must pass (rules E, F, W; E501 ignored). Runner scripts need `# noqa: E402` on imports that follow the `sys.path` insertion.
- No Python 3.12+ syntax (CI is 3.11). `X | None` annotations are fine.
- Commits end with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Sonnet 5 thinking blocks.** `claude-sonnet-5` (production's `EXTRACTION_MODEL` / `MATCH_MODEL`) runs adaptive thinking when `thinking` is omitted, so `response.content[0]` can be a thinking block with no `.text`. Today that raises `AttributeError` inside the `try`, which production turns into an `*APIError`, so the eval would mark those examples `errored` instead of grading them. Expected: the first text block is read, and a response with no text block is a `*ResponseError`. Pinned in Task 1.
2. **YAML auto-typing.** An unquoted `effective_date: 2027-05-31` loads as a `datetime.date`, not a string, and would never equal the model's `"2027-05-31"`. Expected: the loader normalizes it to an ISO string. Pinned in Task 2.
3. **Entity shape drift.** The model writes the free-form `entity.type` however it likes, varies casing (`"oracle"` vs `"Oracle"`), and may return `{"type": null, "company": null, "product": null}` to mean "no entity". Expected: only the keys the golden file names are compared, case- and whitespace-insensitively, and an all-null entity counts as null. Pinned in Task 4.
4. **Malformed matcher output.** The model may return a JSON object instead of an array, skip a candidate index, repeat one, or set both `matched_topic_id` and `new_topic`. Expected: the scenario is `failed` with a legible reason. The harness must not crash. Pinned in Task 5.
5. **A `--filter` that matches nothing** (a typo). Expected: a message listing the available names and a non-zero exit before any credentials lookup or API call, not an empty report that looks like success. Pinned in Task 2 (`filter_examples`) and verified in Task 7.

---

## File Structure

| File | Responsibility |
|---|---|
| `extract.py`, `match.py` (modify) | Read the first text block, and give thinking some token headroom (Task 1) |
| `evals/__init__.py` (create, empty) | Makes `evals` importable from `tests/` and the runners |
| `evals/harness.py` (create) | Golden dataclasses, loader + schema validation, filtering, grading, aggregation, report formatting |
| `evals/judge.py` (create) | Judge prompt, judge API call, verdict parsing |
| `evals/run_extraction_eval.py`, `evals/run_matching_eval.py` (create) | CLI entry points |
| `evals/golden/extraction/database_development.yaml` (create) | Seed extraction examples |
| `evals/golden/matching/*.yaml` (create) | Seed matching scenarios |
| `tests/test_evals_harness.py`, `tests/test_evals_judge.py`, `tests/test_evals_golden.py` (create) | Unit tests (fakes only) |
| `README.md` (modify) | "Evals" section |

**Branching:** the prerequisite production changes (commit `fb881c2`) are on
`fix/eval-readiness-matcher-errors`. Create the implementation branch from there:
`git checkout -b feat/extraction-matching-evals`.

---

### Task 1: Read the text block in production (thinking-safe responses)

**Files:**
- Modify: `extract.py` (the `try` block in `extract_topic`, around lines 69-76)
- Modify: `match.py` (the `try` block in `call_matcher`, around lines 67-75, and the `MatchResponseError` docstring)
- Modify: `tests/test_extract.py`, `tests/test_match.py`, `tests/test_end_to_end.py` (fakes)

**Interfaces:**
- Consumes: nothing new.
- Produces: `extract.extract_topic` and `match.call_matcher` raise `ExtractionResponseError` / `MatchResponseError` (message contains `"no text block"`) when a response has no `type == "text"` block. Test fakes' `FakeContentBlock` gains a `type = "text"` class attribute. Every fake used from here on must have one.

- [ ] **Step 1: Give the existing fakes a block type and let them return prebuilt responses**

In **each** of `tests/test_extract.py`, `tests/test_match.py`, `tests/test_end_to_end.py`, change `FakeContentBlock` to:

```python
class FakeContentBlock:
    type = "text"

    def __init__(self, text):
        self.text = text
```

In `tests/test_extract.py` and `tests/test_match.py`, change `FakeMessages.create` to:

```python
    def create(self, **kwargs):
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(result)
```

and add below `FakeClient`:

```python
class FakeThinkingBlock:
    type = "thinking"
    thinking = ""
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_extract.py`:

```python
def test_extract_topic_reads_text_block_after_thinking_block():
    response = FakeResponse(json.dumps(
        {"signal_type": "new_feature_demand", "summary": "User wants a dark theme", "confidence": 0.9}
    ))
    response.content.insert(0, FakeThinkingBlock())
    client = FakeClient([response])

    result = extract.extract_topic(client, SAMPLE_EVIDENCE)

    assert result["signal_type"] == "new_feature_demand"


def test_extract_topic_raises_response_error_when_no_text_block():
    response = FakeResponse("unused")
    response.content = [FakeThinkingBlock()]
    client = FakeClient([response])

    with pytest.raises(extract.ExtractionResponseError, match="no text block"):
        extract.extract_topic(client, SAMPLE_EVIDENCE)
```

Append to `tests/test_match.py`:

```python
def test_call_matcher_reads_text_block_after_thinking_block():
    response = FakeResponse(json.dumps([{"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None}]))
    response.content.insert(0, FakeThinkingBlock())
    client = FakeClient([response])

    decisions = match.call_matcher(client, CANDIDATES, EXISTING_TOPICS)

    assert decisions == [{"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None}]


def test_call_matcher_raises_response_error_when_no_text_block():
    response = FakeResponse("unused")
    response.content = [FakeThinkingBlock()]
    client = FakeClient([response])

    with pytest.raises(match.MatchResponseError, match="no text block"):
        match.call_matcher(client, CANDIDATES, EXISTING_TOPICS)
```

- [ ] **Step 3: Run the new tests and confirm they fail**

Run: `python -m pytest tests/test_extract.py tests/test_match.py -k "text_block" -v`
Expected: 4 FAIL. The "after thinking block" tests fail with `ExtractionAPIError` / `MatchAPIError` (the `AttributeError` gets wrapped). The "no text block" tests fail because the wrong exception type is raised.

- [ ] **Step 4: Implement in `extract.py`**

Replace the `try`/`except` block in `extract_topic` with:

```python
    try:
        response = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise ExtractionAPIError(f"evidence {evidence_label}: API call failed: {e}") from e

    # Sonnet 5 runs adaptive thinking by default, so a thinking block may precede the answer.
    raw_text = next((block.text for block in response.content if block.type == "text"), None)
    if raw_text is None:
        raise ExtractionResponseError(
            f"evidence {evidence_label}: response has no text block "
            f"(stop_reason={getattr(response, 'stop_reason', None)!r})"
        )
```

(`max_tokens` goes from 500 to 4000 so that thinking cannot use up the budget before the JSON answer. You pay only for the tokens actually generated.)

- [ ] **Step 5: Implement in `match.py`**

Replace the `try`/`except` block in `call_matcher` with:

```python
    try:
        response = client.messages.create(
            model=MATCH_MODEL,
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise MatchAPIError(f"matching call failed: {e}") from e

    # Sonnet 5 runs adaptive thinking by default, so a thinking block may precede the answer.
    raw_text = next((block.text for block in response.content if block.type == "text"), None)
    if raw_text is None:
        raise MatchResponseError(
            f"matching response has no text block (stop_reason={getattr(response, 'stop_reason', None)!r})"
        )
```

and change the `MatchResponseError` docstring to:

```python
    """The model responded, but the response was unusable (no text block, or not valid JSON)."""
```

- [ ] **Step 6: Run the full suite and lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass (106 tests), ruff reports no errors.

- [ ] **Step 7: Commit**

```bash
git add extract.py match.py tests/test_extract.py tests/test_match.py tests/test_end_to_end.py
git commit -m "fix: read the text block from Claude responses so thinking blocks don't break parsing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Golden-set dataclasses, loader, schema validation, filtering

**Files:**
- Create: `evals/__init__.py` (empty)
- Create: `evals/harness.py`
- Create: `evals/judge.py` (stub: only the error classes and `JudgeVerdict`, so `harness.py` can import it. Task 3 fills in the rest.)
- Test: `tests/test_evals_harness.py`

**Interfaces:**
- Consumes: `extract.SIGNAL_TYPES` (a `set[str]`).
- Produces:
  - `harness.GoldenSchemaError(Exception)`
  - `@dataclass ExtractionExample(id: str, source: str, evidence: dict, expected: dict, reference_summary: str | None, notes: str | None = None)`. `source` is the golden file's stem (e.g. `"database_development"`). `evidence` is `{"title": str, "content": str}`. `expected` is either `{"skip": True}` or `{"skip": False, "signal_type": str, "entity": dict | None, "effective_date": str | None}` (date already normalized to `"YYYY-MM-DD"`).
  - `@dataclass MatchingScenario(name: str, source: str, existing_topics: list[dict], candidates: list[dict], expected: list[dict], notes: str | None = None)`. `expected` is sorted by index and is exactly `[{"index": int, "matched_topic_id": str | None}, ...]`, one per candidate.
  - `harness.load_golden_dir(path, kind: str) -> list[ExtractionExample] | list[MatchingScenario]`, where `kind` is `"extraction"` or `"matching"`.
  - `harness.filter_examples(items, name: str | None) -> list`

- [ ] **Step 1: Create the package and the judge stub**

`evals/__init__.py`: empty file.

`evals/judge.py`:

```python
"""LLM-as-judge for the extraction eval's free-text summary field."""
from dataclasses import dataclass


class JudgeError(Exception):
    pass


class JudgeAPIError(JudgeError):
    """The judge API call itself failed -- the example is errored, not graded."""


class JudgeResponseError(JudgeError):
    """The judge responded, but its verdict could not be parsed."""


@dataclass
class JudgeVerdict:
    passed: bool
    reason: str
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_evals_harness.py`:

```python
import textwrap

import pytest

from evals import harness


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


VALID_EXTRACTION = """
    product_area: database_development
    examples:
      - id: db-001
        evidence:
          title: "Migration tool times out"
          content: "It hangs on 40M-row tables."
        expected:
          skip: false
          signal_type: reliability_issue
          entity: null
          effective_date: null
        reference_summary: >
          Migration tool times out on large tables.
      - id: db-002
        evidence:
          title: "Crypto bot!!!"
          content: "DM me"
        expected:
          skip: true
"""

VALID_MATCHING = """
    name: pooling
    existing_topics:
      - topic_id: TOPIC-0001
        name: "Connection pooling exhaustion"
        description: "Max-connections errors under load"
    candidates:
      - signal_type: reliability_issue
        summary: "Runs out of DB connections during spikes"
      - signal_type: new_feature_demand
        summary: "Wants CSV export"
    expected:
      - index: 0
        matched_topic_id: TOPIC-0001
        new_topic: null
      - index: 1
        matched_topic_id: null
        new_topic:
          name: "CSV export"
"""


# --- extraction loading ---

def test_load_extraction_dir_parses_examples(tmp_path):
    write(tmp_path, "database_development.yaml", VALID_EXTRACTION)

    examples = harness.load_golden_dir(tmp_path, "extraction")

    assert [e.id for e in examples] == ["db-001", "db-002"]
    first, second = examples
    assert first.source == "database_development"
    assert first.evidence == {"title": "Migration tool times out", "content": "It hangs on 40M-row tables."}
    assert first.expected == {
        "skip": False, "signal_type": "reliability_issue", "entity": None, "effective_date": None,
    }
    assert first.reference_summary == "Migration tool times out on large tables."
    assert second.expected == {"skip": True}
    assert second.reference_summary is None


def test_load_extraction_normalizes_unquoted_yaml_date(tmp_path):
    write(tmp_path, "db.yaml", VALID_EXTRACTION.replace("effective_date: null", "effective_date: 2027-05-31"))

    examples = harness.load_golden_dir(tmp_path, "extraction")

    assert examples[0].expected["effective_date"] == "2027-05-31"


def test_load_extraction_keeps_partial_entity(tmp_path):
    write(tmp_path, "db.yaml", VALID_EXTRACTION.replace("entity: null", "entity: {company: Oracle}"))

    examples = harness.load_golden_dir(tmp_path, "extraction")

    assert examples[0].expected["entity"] == {"company": "Oracle"}


def test_load_extraction_rejects_invalid_signal_type(tmp_path):
    write(tmp_path, "db.yaml", VALID_EXTRACTION.replace("signal_type: reliability_issue", "signal_type: made_up"))

    with pytest.raises(harness.GoldenSchemaError, match=r"db\.yaml \[db-001\].*invalid expected signal_type"):
        harness.load_golden_dir(tmp_path, "extraction")


def test_load_extraction_rejects_missing_expected_key(tmp_path):
    write(tmp_path, "db.yaml", VALID_EXTRACTION.replace("          entity: null\n", ""))

    with pytest.raises(harness.GoldenSchemaError, match=r"\[db-001\].*'expected.entity' is required"):
        harness.load_golden_dir(tmp_path, "extraction")


def test_load_extraction_rejects_unknown_expected_key(tmp_path):
    write(tmp_path, "db.yaml", VALID_EXTRACTION.replace("signal_type: reliability_issue", "signal-type: reliability_issue"))

    with pytest.raises(harness.GoldenSchemaError, match=r"\[db-001\].*unknown keys"):
        harness.load_golden_dir(tmp_path, "extraction")


def test_load_extraction_rejects_missing_reference_summary(tmp_path):
    text = VALID_EXTRACTION.replace("        reference_summary: >\n          Migration tool times out on large tables.\n", "")
    write(tmp_path, "db.yaml", text)

    with pytest.raises(harness.GoldenSchemaError, match=r"\[db-001\].*'reference_summary'"):
        harness.load_golden_dir(tmp_path, "extraction")


def test_load_extraction_rejects_bad_date(tmp_path):
    write(tmp_path, "db.yaml", VALID_EXTRACTION.replace("effective_date: null", 'effective_date: "next spring"'))

    with pytest.raises(harness.GoldenSchemaError, match=r"\[db-001\].*effective_date"):
        harness.load_golden_dir(tmp_path, "extraction")


def test_load_extraction_rejects_duplicate_ids_across_files(tmp_path):
    write(tmp_path, "a.yaml", VALID_EXTRACTION)
    write(tmp_path, "b.yaml", VALID_EXTRACTION)

    with pytest.raises(harness.GoldenSchemaError, match=r"b\.yaml \[db-001\].*duplicate.*a\.yaml"):
        harness.load_golden_dir(tmp_path, "extraction")


def test_load_rejects_invalid_yaml(tmp_path):
    write(tmp_path, "db.yaml", "examples: [unclosed\n")

    with pytest.raises(harness.GoldenSchemaError, match=r"db\.yaml.*invalid YAML"):
        harness.load_golden_dir(tmp_path, "extraction")


def test_load_rejects_empty_dir(tmp_path):
    with pytest.raises(harness.GoldenSchemaError, match="no .yaml golden files"):
        harness.load_golden_dir(tmp_path, "extraction")


# --- matching loading ---

def test_load_matching_dir_parses_scenario(tmp_path):
    write(tmp_path, "pooling.yaml", VALID_MATCHING)

    [scenario] = harness.load_golden_dir(tmp_path, "matching")

    assert scenario.name == "pooling"
    assert scenario.source == "pooling"
    assert [t["topic_id"] for t in scenario.existing_topics] == ["TOPIC-0001"]
    assert len(scenario.candidates) == 2
    assert scenario.expected == [
        {"index": 0, "matched_topic_id": "TOPIC-0001"},
        {"index": 1, "matched_topic_id": None},
    ]


def test_load_matching_rejects_unknown_expected_topic_id(tmp_path):
    write(tmp_path, "pooling.yaml", VALID_MATCHING.replace("matched_topic_id: TOPIC-0001", "matched_topic_id: TOPIC-9999"))

    with pytest.raises(harness.GoldenSchemaError, match=r"\[pooling\].*TOPIC-9999.*not in existing_topics"):
        harness.load_golden_dir(tmp_path, "matching")


def test_load_matching_rejects_missing_expected_index(tmp_path):
    text = VALID_MATCHING.split("      - index: 1")[0]
    write(tmp_path, "pooling.yaml", text)

    with pytest.raises(harness.GoldenSchemaError, match=r"\[pooling\].*no expected decision for candidate indexes \[1\]"):
        harness.load_golden_dir(tmp_path, "matching")


def test_load_matching_rejects_duplicate_expected_index(tmp_path):
    write(tmp_path, "pooling.yaml", VALID_MATCHING.replace("- index: 1", "- index: 0"))

    with pytest.raises(harness.GoldenSchemaError, match=r"\[pooling\].*duplicate expected index 0"):
        harness.load_golden_dir(tmp_path, "matching")


def test_load_matching_rejects_both_match_and_new_topic(tmp_path):
    text = VALID_MATCHING.replace(
        "matched_topic_id: TOPIC-0001\n        new_topic: null",
        "matched_topic_id: TOPIC-0001\n        new_topic: {name: x}",
    )
    write(tmp_path, "pooling.yaml", text)

    with pytest.raises(harness.GoldenSchemaError, match=r"\[pooling\].*cannot set both"):
        harness.load_golden_dir(tmp_path, "matching")


def test_load_matching_rejects_invalid_candidate_signal_type(tmp_path):
    write(tmp_path, "pooling.yaml", VALID_MATCHING.replace("signal_type: new_feature_demand", "signal_type: nope"))

    with pytest.raises(harness.GoldenSchemaError, match=r"\[pooling\].*invalid candidate signal_type"):
        harness.load_golden_dir(tmp_path, "matching")


def test_load_matching_rejects_duplicate_scenario_names(tmp_path):
    write(tmp_path, "a.yaml", VALID_MATCHING)
    write(tmp_path, "b.yaml", VALID_MATCHING)

    with pytest.raises(harness.GoldenSchemaError, match="duplicate"):
        harness.load_golden_dir(tmp_path, "matching")


# --- filtering ---

def test_filter_examples_by_extraction_file_stem(tmp_path):
    write(tmp_path, "database_development.yaml", VALID_EXTRACTION)
    write(tmp_path, "other_area.yaml", VALID_EXTRACTION.replace("db-00", "oa-00"))
    examples = harness.load_golden_dir(tmp_path, "extraction")

    assert [e.id for e in harness.filter_examples(examples, "other_area")] == ["oa-001", "oa-002"]
    assert len(harness.filter_examples(examples, None)) == 4
    assert harness.filter_examples(examples, "typo") == []


def test_filter_examples_by_matching_scenario_name(tmp_path):
    write(tmp_path, "file_stem_differs.yaml", VALID_MATCHING)
    scenarios = harness.load_golden_dir(tmp_path, "matching")

    assert [s.name for s in harness.filter_examples(scenarios, "pooling")] == ["pooling"]
    assert harness.filter_examples(scenarios, "file_stem_differs") == []
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_harness.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'evals.harness'` (or `AttributeError` once the file exists).

- [ ] **Step 4: Implement `evals/harness.py` (loading and filtering part)**

```python
"""Golden-set loading, grading, and aggregation for the extraction/matching evals.

Everything here is deterministic Python. The API-calling code lives in extract.py,
match.py and evals/judge.py.
"""
import datetime
from dataclasses import dataclass
from pathlib import Path

import yaml

import extract

ENTITY_KEYS = {"type", "company", "product"}
EXTRACTION_EXPECTED_KEYS = {"skip", "signal_type", "entity", "effective_date"}


class GoldenSchemaError(Exception):
    """A golden file is malformed -- a bug in the eval data, not a model result."""


@dataclass
class ExtractionExample:
    id: str
    source: str
    evidence: dict
    expected: dict
    reference_summary: str | None
    notes: str | None = None


@dataclass
class MatchingScenario:
    name: str
    source: str
    existing_topics: list
    candidates: list
    expected: list
    notes: str | None = None


def load_golden_dir(path, kind):
    """Load and validate every .yaml/.yml file in `path`. `kind` is "extraction" or "matching"."""
    loaders = {"extraction": _load_extraction_file, "matching": _load_matching_file}
    if kind not in loaders:
        raise ValueError(f"unknown golden kind {kind!r}")

    files = sorted(p for p in Path(path).iterdir() if p.suffix in (".yaml", ".yml"))
    if not files:
        raise GoldenSchemaError(f"{path}: no .yaml golden files found")

    items, seen = [], {}
    for file_path in files:
        for item in loaders[kind](file_path, _read_yaml(file_path)):
            key = item.id if kind == "extraction" else item.name
            if key in seen:
                what = "id" if kind == "extraction" else "scenario name"
                _fail(file_path, key, f"duplicate {what}, also defined in {seen[key]}")
            seen[key] = file_path.name
            items.append(item)
    return items


def filter_examples(items, name):
    """Keep extraction examples whose golden file stem is `name`, or the matching scenario named `name`."""
    if name is None:
        return list(items)
    return [item for item in items if (item.source if isinstance(item, ExtractionExample) else item.name) == name]


def _read_yaml(file_path):
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise GoldenSchemaError(f"{file_path.name}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise GoldenSchemaError(f"{file_path.name}: top level must be a mapping")
    return data


def _fail(file_path, item_id, message):
    where = f"{file_path.name} [{item_id}]" if item_id else file_path.name
    raise GoldenSchemaError(f"{where}: {message}")


def _require_str(file_path, item_id, mapping, key, allow_empty=False):
    value = mapping.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(file_path, item_id, f"'{key}' must be a {'' if allow_empty else 'non-empty '}string")
    return value


def _normalize_date(file_path, item_id, value):
    if value is None:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            pass
        else:
            return value
    _fail(file_path, item_id, f"'expected.effective_date' must be null or a YYYY-MM-DD date, got {value!r}")


def _parse_entity(file_path, item_id, value):
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        _fail(file_path, item_id, "'expected.entity' must be null or a mapping with any of type/company/product")
    unknown = value.keys() - ENTITY_KEYS
    if unknown:
        _fail(file_path, item_id, f"unknown keys in 'expected.entity': {sorted(unknown)}")
    for key, entity_value in value.items():
        if entity_value is not None and not isinstance(entity_value, str):
            _fail(file_path, item_id, f"'expected.entity.{key}' must be a string or null")
    return dict(value)


def _load_extraction_file(file_path, data):
    _require_str(file_path, None, data, "product_area")
    examples = data.get("examples")
    if not isinstance(examples, list) or not examples:
        _fail(file_path, None, "'examples' must be a non-empty list")
    return [_parse_extraction_example(file_path, position, raw) for position, raw in enumerate(examples)]


def _parse_extraction_example(file_path, position, raw):
    if not isinstance(raw, dict):
        _fail(file_path, f"#{position}", "example must be a mapping")
    example_id = raw.get("id")
    if not isinstance(example_id, str) or not example_id.strip():
        _fail(file_path, f"#{position}", "'id' must be a non-empty string")

    evidence = raw.get("evidence")
    if not isinstance(evidence, dict):
        _fail(file_path, example_id, "'evidence' must be a mapping")
    evidence = {
        "title": _require_str(file_path, example_id, evidence, "title"),
        "content": _require_str(file_path, example_id, evidence, "content", allow_empty=True),
    }

    expected = raw.get("expected")
    if not isinstance(expected, dict):
        _fail(file_path, example_id, "'expected' must be a mapping")
    unknown = expected.keys() - EXTRACTION_EXPECTED_KEYS
    if unknown:
        _fail(file_path, example_id, f"unknown keys in 'expected': {sorted(unknown)}")
    skip = expected.get("skip")
    if not isinstance(skip, bool):
        _fail(file_path, example_id, "'expected.skip' must be true or false")

    source = file_path.stem
    notes = raw.get("notes")
    if skip:
        return ExtractionExample(example_id, source, evidence, {"skip": True}, None, notes)

    for key in ("signal_type", "entity", "effective_date"):
        if key not in expected:
            _fail(file_path, example_id, f"'expected.{key}' is required when skip is false (use null for none)")
    if expected["signal_type"] not in extract.SIGNAL_TYPES:
        _fail(file_path, example_id, f"invalid expected signal_type {expected['signal_type']!r}")

    return ExtractionExample(
        id=example_id,
        source=source,
        evidence=evidence,
        expected={
            "skip": False,
            "signal_type": expected["signal_type"],
            "entity": _parse_entity(file_path, example_id, expected["entity"]),
            "effective_date": _normalize_date(file_path, example_id, expected["effective_date"]),
        },
        reference_summary=_require_str(file_path, example_id, raw, "reference_summary").strip(),
        notes=notes,
    )


def _load_matching_file(file_path, data):
    name = _require_str(file_path, None, data, "name")

    topics = data.get("existing_topics")
    if not isinstance(topics, list):
        _fail(file_path, name, "'existing_topics' must be a list (may be empty)")
    topic_ids = set()
    for topic in topics:
        if not isinstance(topic, dict):
            _fail(file_path, name, "each existing topic must be a mapping")
        topic_id = _require_str(file_path, name, topic, "topic_id")
        _require_str(file_path, name, topic, "name")
        _require_str(file_path, name, topic, "description")
        if topic_id in topic_ids:
            _fail(file_path, name, f"duplicate topic_id {topic_id!r}")
        topic_ids.add(topic_id)

    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        _fail(file_path, name, "'candidates' must be a non-empty list")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            _fail(file_path, name, "each candidate must be a mapping")
        if candidate.get("signal_type") not in extract.SIGNAL_TYPES:
            _fail(file_path, name, f"invalid candidate signal_type {candidate.get('signal_type')!r}")
        _require_str(file_path, name, candidate, "summary")

    expected = data.get("expected")
    if not isinstance(expected, list):
        _fail(file_path, name, "'expected' must be a list")
    by_index = {}
    for decision in expected:
        if not isinstance(decision, dict):
            _fail(file_path, name, "each expected decision must be a mapping")
        index = decision.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(candidates):
            _fail(file_path, name, f"expected index {index!r} is not a valid candidate index")
        if index in by_index:
            _fail(file_path, name, f"duplicate expected index {index}")
        if "matched_topic_id" not in decision:
            _fail(file_path, name, f"expected[{index}] needs 'matched_topic_id' (null for a new topic)")
        topic_id = decision["matched_topic_id"]
        new_topic = decision.get("new_topic")
        if topic_id is not None:
            if topic_id not in topic_ids:
                _fail(file_path, name, f"expected[{index}] matched_topic_id {topic_id!r} is not in existing_topics")
            if new_topic is not None:
                _fail(file_path, name, f"expected[{index}] cannot set both matched_topic_id and new_topic")
        elif new_topic is not None and not isinstance(new_topic, dict):
            _fail(file_path, name, f"expected[{index}] new_topic must be null or a mapping")
        by_index[index] = {"index": index, "matched_topic_id": topic_id}

    missing = set(range(len(candidates))) - by_index.keys()
    if missing:
        _fail(file_path, name, f"no expected decision for candidate indexes {sorted(missing)}")

    return [MatchingScenario(
        name=name,
        source=file_path.stem,
        existing_topics=topics,
        candidates=candidates,
        expected=[by_index[i] for i in range(len(candidates))],
        notes=data.get("notes"),
    )]
```

Note: a golden `new_topic` mapping for a new-topic decision is documentation only. The model's generated name and slug can't be exact-matched, so grading only checks that the decision is "new" (see Task 5).

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_evals_harness.py -v`
Expected: all PASS.

- [ ] **Step 6: Full suite + lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add evals/__init__.py evals/harness.py evals/judge.py tests/test_evals_harness.py
git commit -m "feat(evals): golden-set loader with fail-fast schema validation

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: LLM judge for the extraction summary

**Files:**
- Modify: `evals/judge.py` (replace the stub from Task 2)
- Test: `tests/test_evals_judge.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. The error classes and `JudgeVerdict` keep the names from the Task 2 stub.
- Produces:
  - `judge.JUDGE_MODEL = "claude-opus-5"`
  - `judge.build_judge_prompt(evidence: dict, reference_summary: str, actual_summary: str) -> str`
  - `judge.judge_summary(evidence: dict, reference_summary: str, actual_summary: str, client) -> JudgeVerdict`. Raises `JudgeAPIError` if the call fails. Raises `JudgeResponseError` on a refusal, a `max_tokens` stop, no text block, or an unparseable verdict.
  - `judge.parse_judge_response(raw_text: str) -> JudgeVerdict`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_evals_judge.py`:

```python
import json

import pytest

from evals import judge


class FakeContentBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeThinkingBlock:
    type = "thinking"
    thinking = ""


class FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeContentBlock(text)]
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(result)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


EVIDENCE = {"title": "Migration tool times out", "content": "It hangs on 40M-row tables, no error."}
REFERENCE = "Migration tool times out on large tables with no error message."
ACTUAL = "Migrations hang on very large tables without an error."


def test_judge_summary_returns_pass_verdict_and_sends_prompt():
    client = FakeClient([json.dumps({"verdict": "pass", "reason": "Faithful and on-topic."})])

    verdict = judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)

    assert verdict == judge.JudgeVerdict(passed=True, reason="Faithful and on-topic.")
    call = client.messages.calls[0]
    assert call["model"] == judge.JUDGE_MODEL
    prompt = call["messages"][0]["content"]
    for text in (EVIDENCE["title"], EVIDENCE["content"], REFERENCE, ACTUAL):
        assert text in prompt


def test_judge_summary_returns_fail_verdict():
    client = FakeClient([json.dumps({"verdict": "fail", "reason": "Invents a cause."})])

    verdict = judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)

    assert verdict == judge.JudgeVerdict(passed=False, reason="Invents a cause.")


def test_judge_summary_reads_text_block_after_thinking_block():
    response = FakeResponse(json.dumps({"verdict": "pass", "reason": "ok"}))
    response.content.insert(0, FakeThinkingBlock())
    client = FakeClient([response])

    assert judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client).passed is True


def test_judge_summary_wraps_api_failure():
    client = FakeClient([RuntimeError("529 overloaded")])

    with pytest.raises(judge.JudgeAPIError, match="529 overloaded"):
        judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_judge_summary_rejects_bad_stop_reason(stop_reason):
    client = FakeClient([FakeResponse("{}", stop_reason=stop_reason)])

    with pytest.raises(judge.JudgeResponseError, match=stop_reason):
        judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)


def test_judge_summary_rejects_response_without_text_block():
    response = FakeResponse("unused")
    response.content = [FakeThinkingBlock()]
    client = FakeClient([response])

    with pytest.raises(judge.JudgeResponseError, match="no text block"):
        judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)


def test_parse_judge_response_accepts_code_fence_and_case():
    raw = '```json\n{"verdict": "PASS", "reason": "  fine  "}\n```'

    assert judge.parse_judge_response(raw) == judge.JudgeVerdict(passed=True, reason="fine")


@pytest.mark.parametrize("raw", [
    "The summary looks good.",
    json.dumps(["pass"]),
    json.dumps({"verdict": "maybe", "reason": "?"}),
    json.dumps({"reason": "no verdict"}),
])
def test_parse_judge_response_rejects_unusable_output(raw):
    with pytest.raises(judge.JudgeResponseError):
        judge.parse_judge_response(raw)


def test_parse_judge_response_tolerates_missing_reason():
    assert judge.parse_judge_response(json.dumps({"verdict": "fail"})) == judge.JudgeVerdict(passed=False, reason="")
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_judge.py -v`
Expected: FAIL with `AttributeError: module 'evals.judge' has no attribute 'judge_summary'` (and similar).

- [ ] **Step 3: Implement `evals/judge.py`**

Replace the whole file with:

```python
"""LLM-as-judge for the extraction eval's free-text summary field."""
import json
import re
from dataclasses import dataclass

JUDGE_MODEL = "claude-opus-5"
JUDGE_MAX_TOKENS = 16000

JUDGE_PROMPT_TEMPLATE = """You are grading one field of an automated product-feedback extractor.

The extractor read a Reddit post and wrote a one-line summary of what the post is saying. A human \
wrote a reference summary for the same post. Decide whether the extractor's summary is acceptable.

Original post title: {title}
Original post body: {content}

Reference summary (human-written): {reference_summary}

Extractor's summary: {actual_summary}

The extractor's summary PASSES if all of these hold:
1. Faithful: every claim in it is supported by the post. It adds no details, causes, numbers, \
products, or sentiment that the post does not state.
2. On-topic: it describes the same core issue or request as the reference summary. Different \
wording, more or less detail, or a different sentence structure is fine.
3. Useful: a product manager reading only this summary would understand what the user is saying.

It FAILS if it invents details, misrepresents what the user said, focuses on a side point \
instead of the main point, or is too vague to act on.

Respond with ONLY a JSON object with these exact keys:
- "verdict": "pass" or "fail"
- "reason": one sentence explaining the verdict
"""

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class JudgeError(Exception):
    pass


class JudgeAPIError(JudgeError):
    """The judge API call itself failed -- the example is errored, not graded."""


class JudgeResponseError(JudgeError):
    """The judge responded, but its verdict could not be parsed."""


@dataclass
class JudgeVerdict:
    passed: bool
    reason: str


def build_judge_prompt(evidence, reference_summary, actual_summary):
    return JUDGE_PROMPT_TEMPLATE.format(
        title=evidence["title"], content=evidence["content"],
        reference_summary=reference_summary, actual_summary=actual_summary,
    )


def judge_summary(evidence, reference_summary, actual_summary, client):
    prompt = build_judge_prompt(evidence, reference_summary, actual_summary)
    try:
        response = client.messages.create(
            model=JUDGE_MODEL,
            max_tokens=JUDGE_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        raise JudgeAPIError(f"judge call failed: {e}") from e

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason in ("refusal", "max_tokens"):
        raise JudgeResponseError(f"judge stopped with stop_reason={stop_reason!r}")

    # Opus 5 runs adaptive thinking by default, so a thinking block may precede the answer.
    raw_text = next((block.text for block in response.content if block.type == "text"), None)
    if raw_text is None:
        raise JudgeResponseError("judge response has no text block")
    return parse_judge_response(raw_text)


def parse_judge_response(raw_text):
    text = raw_text.strip()
    fenced = _CODE_FENCE_RE.match(text)
    if fenced:
        text = fenced.group(1)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        raise JudgeResponseError(f"judge returned non-JSON response: {raw_text!r}") from e
    if not isinstance(parsed, dict):
        raise JudgeResponseError(f"judge returned {type(parsed).__name__}, expected an object: {raw_text!r}")

    verdict = parsed.get("verdict")
    normalized = verdict.strip().lower() if isinstance(verdict, str) else None
    if normalized not in ("pass", "fail"):
        raise JudgeResponseError(f"judge verdict must be 'pass' or 'fail', got {verdict!r}")

    reason = parsed.get("reason")
    return JudgeVerdict(passed=normalized == "pass", reason=reason.strip() if isinstance(reason, str) else "")
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_evals_judge.py -v`
Expected: all PASS.

- [ ] **Step 5: Full suite + lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add evals/judge.py tests/test_evals_judge.py
git commit -m "feat(evals): LLM judge for extraction summaries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Grade extraction examples

**Files:**
- Modify: `evals/harness.py`
- Test: `tests/test_evals_harness.py` (append)

**Interfaces:**
- Consumes: `ExtractionExample` (Task 2). `judge.judge_summary`, `judge.JudgeError` (Task 3). `extract.extract_topic(client, evidence) -> dict | None`, which raises `extract.ExtractionAPIError` / `extract.ExtractionResponseError` and returns `{"signal_type", "summary", "confidence", "entity", "effective_date"}`.
- Produces:
  - `@dataclass FieldResult(field: str, label: str, expected: object, actual: object, passed: bool, detail: str | None = None)`. `field` is the aggregation key. `label` is what the report prints.
  - `@dataclass ExampleResult(example_id: str, source: str, status: str, fields: list[FieldResult] = [], error: str | None = None)`. `status` is one of `"passed"`, `"failed"`, `"errored"`.
  - `harness.entity_matches(expected: dict | None, actual: dict | None) -> bool`
  - `harness.grade_extraction_example(example: ExtractionExample, client) -> ExampleResult`. Field names are, in order: `skip`, `signal_type`, `entity`, `effective_date`, `summary`. Only `skip` is graded when either side skipped.

- [ ] **Step 1: Write the failing tests**

Add to the imports at the top of `tests/test_evals_harness.py`:

```python
import dataclasses
import json
```

and append:

```python
# --- fakes (repo FakeClient pattern; blocks carry a type because production reads the text block) ---

class FakeContentBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResponse:
    stop_reason = "end_turn"

    def __init__(self, text):
        self.content = [FakeContentBlock(text)]


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)

    def create(self, **kwargs):
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


# --- extraction grading ---

EXAMPLE = harness.ExtractionExample(
    id="db-001",
    source="database_development",
    evidence={"title": "Migration tool times out", "content": "It hangs on 40M-row tables."},
    expected={"skip": False, "signal_type": "reliability_issue", "entity": None, "effective_date": None},
    reference_summary="Migration tool times out on large tables.",
)
SKIP_EXAMPLE = dataclasses.replace(EXAMPLE, id="db-002", expected={"skip": True}, reference_summary=None)

JUDGE_PASS = json.dumps({"verdict": "pass", "reason": "Faithful."})
JUDGE_FAIL = json.dumps({"verdict": "fail", "reason": "Adds a cause the post never states."})


def extraction_response(**overrides):
    body = {
        "signal_type": "reliability_issue", "summary": "Migrations hang on large tables",
        "confidence": 0.9, "entity": None, "effective_date": None,
    }
    body.update(overrides)
    return json.dumps(body)


def test_grade_extraction_all_fields_match():
    client = FakeClient([extraction_response(), JUDGE_PASS])

    result = harness.grade_extraction_example(EXAMPLE, client)

    assert result.status == "passed"
    assert result.example_id == "db-001"
    assert result.source == "database_development"
    assert [f.field for f in result.fields] == ["skip", "signal_type", "entity", "effective_date", "summary"]
    assert all(f.passed for f in result.fields)


def test_grade_extraction_signal_type_mismatch_fails_with_expected_and_actual():
    client = FakeClient([extraction_response(signal_type="usability_issue"), JUDGE_PASS])

    result = harness.grade_extraction_example(EXAMPLE, client)

    assert result.status == "failed"
    [miss] = [f for f in result.fields if not f.passed]
    assert (miss.field, miss.expected, miss.actual) == ("signal_type", "reliability_issue", "usability_issue")


def test_grade_extraction_judge_fail_records_reason():
    client = FakeClient([extraction_response(), JUDGE_FAIL])

    result = harness.grade_extraction_example(EXAMPLE, client)

    assert result.status == "failed"
    summary = result.fields[-1]
    assert summary.field == "summary"
    assert summary.passed is False
    assert summary.actual == "Migrations hang on large tables"
    assert summary.detail == "Adds a cause the post never states."


def test_grade_extraction_expected_skip_and_model_skips_passes_without_judge():
    client = FakeClient([json.dumps({"skip": True})])  # no judge response scripted

    result = harness.grade_extraction_example(SKIP_EXAMPLE, client)

    assert result.status == "passed"
    assert [f.field for f in result.fields] == ["skip"]


def test_grade_extraction_expected_skip_but_model_extracts_fails():
    client = FakeClient([extraction_response()])

    result = harness.grade_extraction_example(SKIP_EXAMPLE, client)

    assert result.status == "failed"
    assert [(f.field, f.expected, f.actual) for f in result.fields] == [("skip", True, False)]


def test_grade_extraction_model_skips_unexpectedly_fails_on_skip_only():
    client = FakeClient([json.dumps({"skip": True})])

    result = harness.grade_extraction_example(EXAMPLE, client)

    assert result.status == "failed"
    assert [(f.field, f.expected, f.actual) for f in result.fields] == [("skip", False, True)]


def test_grade_extraction_unusable_response_is_failed_and_names_example():
    client = FakeClient(["not json at all"])

    result = harness.grade_extraction_example(EXAMPLE, client)

    assert result.status == "failed"
    assert "db-001" in result.error
    assert result.fields == []


def test_grade_extraction_api_error_is_errored():
    client = FakeClient([RuntimeError("rate limited")])

    result = harness.grade_extraction_example(EXAMPLE, client)

    assert result.status == "errored"
    assert "db-001" in result.error
    assert "rate limited" in result.error


def test_grade_extraction_judge_api_error_is_errored():
    client = FakeClient([extraction_response(), RuntimeError("judge overloaded")])

    result = harness.grade_extraction_example(EXAMPLE, client)

    assert result.status == "errored"
    assert result.error.startswith("judge:")


def test_grade_extraction_entity_graded_on_named_keys_only():
    example = dataclasses.replace(EXAMPLE, expected={**EXAMPLE.expected, "entity": {"company": "Oracle"}})
    entity = {"type": "vendor", "company": " oracle ", "product": "Oracle Database 19c"}
    client = FakeClient([extraction_response(entity=entity), JUDGE_PASS])

    result = harness.grade_extraction_example(example, client)

    assert result.status == "passed"


def test_grade_extraction_effective_date_mismatch_fails():
    example = dataclasses.replace(EXAMPLE, expected={**EXAMPLE.expected, "effective_date": "2027-04-30"})
    client = FakeClient([extraction_response(effective_date="2027-05-01"), JUDGE_PASS])

    result = harness.grade_extraction_example(example, client)

    [miss] = [f for f in result.fields if not f.passed]
    assert (miss.field, miss.expected, miss.actual) == ("effective_date", "2027-04-30", "2027-05-01")


@pytest.mark.parametrize("expected, actual, matches", [
    (None, None, True),
    (None, {"type": None, "company": None, "product": None}, True),
    (None, {}, True),
    (None, {"type": "vendor", "company": "Oracle", "product": None}, False),
    ({"company": "Oracle"}, None, False),
    ({"company": "Oracle"}, {"type": "x", "company": "ORACLE", "product": "DB"}, True),
    ({"company": "Oracle", "product": "Database 19c"}, {"company": "Oracle", "product": "Database 21c"}, False),
    ({"company": "Oracle", "product": None}, {"company": "Oracle"}, True),
])
def test_entity_matches(expected, actual, matches):
    assert harness.entity_matches(expected, actual) is matches
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_harness.py -v -k "grade_extraction or entity_matches"`
Expected: FAIL with `AttributeError: module 'evals.harness' has no attribute 'grade_extraction_example'` / `'ExtractionExample'`-related errors for `entity_matches`.

- [ ] **Step 3: Implement in `evals/harness.py`**

Change the imports at the top to:

```python
import datetime
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import extract
from evals import judge
```

Add after the `MatchingScenario` dataclass:

```python
@dataclass
class FieldResult:
    field: str
    label: str
    expected: object
    actual: object
    passed: bool
    detail: str | None = None


@dataclass
class ExampleResult:
    example_id: str
    source: str
    status: str
    fields: list = field(default_factory=list)
    error: str | None = None
```

Add at the end of the file:

```python
def grade_extraction_example(example, client):
    try:
        actual = extract.extract_topic(client, {**example.evidence, "evidence_id": example.id})
    except extract.ExtractionAPIError as e:
        return ExampleResult(example.id, example.source, "errored", error=str(e))
    except extract.ExtractionResponseError as e:
        return ExampleResult(example.id, example.source, "failed", error=str(e))

    expected = example.expected
    actual_skip = actual is None
    fields = [_exact("skip", expected["skip"], actual_skip)]
    if expected["skip"] or actual_skip:
        return _finish(example.id, example.source, fields)

    fields.append(_exact("signal_type", expected["signal_type"], actual["signal_type"]))
    fields.append(FieldResult(
        "entity", "entity", expected["entity"], actual["entity"],
        entity_matches(expected["entity"], actual["entity"]),
    ))
    fields.append(_exact("effective_date", expected["effective_date"], actual["effective_date"]))

    try:
        verdict = judge.judge_summary(example.evidence, example.reference_summary, actual["summary"], client)
    except judge.JudgeError as e:
        return ExampleResult(example.id, example.source, "errored", fields, error=f"judge: {e}")
    fields.append(FieldResult(
        "summary", "summary", example.reference_summary, actual["summary"], verdict.passed, detail=verdict.reason,
    ))
    return _finish(example.id, example.source, fields)


def entity_matches(expected, actual):
    """Null must match null. Otherwise each key the golden file names must match, ignoring case
    and surrounding whitespace. Keys the golden file leaves out (usually the free-form 'type')
    are not graded. An entity whose values are all null counts as null."""
    if actual is not None and all(value is None for value in actual.values()):
        actual = None
    if expected is None or actual is None:
        return expected is None and actual is None
    return all(_normalize_text(actual.get(key)) == _normalize_text(value) for key, value in expected.items())


def _normalize_text(value):
    return value.strip().casefold() if isinstance(value, str) else value


def _exact(name, expected, actual):
    return FieldResult(name, name, expected, actual, expected == actual)


def _finish(example_id, source, fields):
    status = "passed" if all(f.passed for f in fields) else "failed"
    return ExampleResult(example_id, source, status, fields)
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_evals_harness.py -v`
Expected: all PASS.

- [ ] **Step 5: Full suite + lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add evals/harness.py tests/test_evals_harness.py
git commit -m "feat(evals): grade extraction examples against golden labels

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Grade matching scenarios

**Files:**
- Modify: `evals/harness.py`
- Test: `tests/test_evals_harness.py` (append)

**Interfaces:**
- Consumes: `MatchingScenario` (Task 2). `FieldResult`, `ExampleResult`, `_finish` (Task 4). `match.call_matcher(client, candidates, existing_topics) -> <parsed JSON>`, which raises `match.MatchAPIError` / `match.MatchResponseError`.
- Produces: `harness.grade_matching_scenario(scenario: MatchingScenario, client) -> ExampleResult`. There is one `FieldResult` per candidate, in index order. Its `field` is `"match_existing"` when the golden decision names a topic and `"new_topic"` when it expects a new topic. Its `label` is `"candidate[<i>]"`, `expected` is the topic id or `"NEW"`, and `actual` is the topic id, `"NEW: <name>"`, or a description of the invalid decision.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evals_harness.py`:

```python
# --- matching grading ---

SCENARIO = harness.MatchingScenario(
    name="pooling",
    source="pooling",
    existing_topics=[{
        "topic_id": "TOPIC-0001", "name": "Connection pooling exhaustion",
        "description": "Max-connections errors under load",
    }],
    candidates=[
        {"signal_type": "reliability_issue", "summary": "Runs out of DB connections during spikes"},
        {"signal_type": "new_feature_demand", "summary": "Wants CSV export"},
    ],
    expected=[{"index": 0, "matched_topic_id": "TOPIC-0001"}, {"index": 1, "matched_topic_id": None}],
)
NEW_TOPIC = {"name": "CSV export", "slug": "csv-export", "description": "User wants CSV export"}


def matcher_response(*decisions):
    return json.dumps(list(decisions))


def test_grade_matching_all_correct():
    client = FakeClient([matcher_response(
        {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None},
        {"index": 1, "matched_topic_id": None, "new_topic": NEW_TOPIC},
    )])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert result.status == "passed"
    assert result.example_id == "pooling"
    assert [(f.field, f.label, f.expected, f.actual) for f in result.fields] == [
        ("match_existing", "candidate[0]", "TOPIC-0001", "TOPIC-0001"),
        ("new_topic", "candidate[1]", "NEW", "NEW: CSV export"),
    ]


def test_grade_matching_duplicate_topic_spawned_fails():
    client = FakeClient([matcher_response(
        {"index": 0, "matched_topic_id": None, "new_topic": {**NEW_TOPIC, "name": "DB connection limits"}},
        {"index": 1, "matched_topic_id": None, "new_topic": NEW_TOPIC},
    )])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert result.status == "failed"
    assert result.fields[0].passed is False
    assert result.fields[0].actual == "NEW: DB connection limits"


def test_grade_matching_wrongly_merged_into_existing_fails():
    client = FakeClient([matcher_response(
        {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None},
        {"index": 1, "matched_topic_id": "TOPIC-0001", "new_topic": None},
    )])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert [f.passed for f in result.fields] == [True, False]


def test_grade_matching_both_match_and_new_topic_fails():
    client = FakeClient([matcher_response(
        {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": NEW_TOPIC},
        {"index": 1, "matched_topic_id": None, "new_topic": NEW_TOPIC},
    )])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert result.fields[0].passed is False
    assert "new_topic also set" in result.fields[0].actual


def test_grade_matching_incomplete_new_topic_fails():
    client = FakeClient([matcher_response(
        {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None},
        {"index": 1, "matched_topic_id": None, "new_topic": {"name": "CSV export"}},
    )])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert result.fields[1].passed is False
    assert result.fields[1].actual.startswith("invalid decision")


def test_grade_matching_missing_and_duplicate_indexes_fail():
    client = FakeClient([matcher_response(
        {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None},
        {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None},
    )])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert result.status == "failed"
    assert [f.actual for f in result.fields] == ["2 decisions for this index", "0 decisions for this index"]


def test_grade_matching_non_array_json_fails():
    client = FakeClient([json.dumps({"index": 0, "matched_topic_id": "TOPIC-0001"})])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert result.status == "failed"
    assert "expected a JSON array" in result.error


def test_grade_matching_non_json_is_failed():
    client = FakeClient(["sorry, I can't"])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert result.status == "failed"
    assert "non-JSON" in result.error


def test_grade_matching_api_error_is_errored():
    client = FakeClient([RuntimeError("connection reset")])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert result.status == "errored"
    assert "connection reset" in result.error
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_harness.py -v -k grade_matching`
Expected: FAIL with `AttributeError: module 'evals.harness' has no attribute 'grade_matching_scenario'`.

- [ ] **Step 3: Implement in `evals/harness.py`**

Add `import match` below `import extract`, then append:

```python
def grade_matching_scenario(scenario, client):
    try:
        decisions = match.call_matcher(client, scenario.candidates, scenario.existing_topics)
    except match.MatchAPIError as e:
        return ExampleResult(scenario.name, scenario.source, "errored", error=str(e))
    except match.MatchResponseError as e:
        return ExampleResult(scenario.name, scenario.source, "failed", error=str(e))

    if not isinstance(decisions, list) or not all(isinstance(d, dict) for d in decisions):
        return ExampleResult(
            scenario.name, scenario.source, "failed",
            error=f"matcher returned {type(decisions).__name__}, expected a JSON array of objects: {decisions!r}",
        )

    decisions_by_index = {}
    for decision in decisions:
        decisions_by_index.setdefault(decision.get("index"), []).append(decision)

    fields = []
    for expected in scenario.expected:
        index = expected["index"]
        want = expected["matched_topic_id"]
        field_name = "match_existing" if want else "new_topic"
        label = f"candidate[{index}]"
        expected_desc = want or "NEW"
        got = decisions_by_index.get(index, [])
        if len(got) != 1:
            fields.append(FieldResult(field_name, label, expected_desc, f"{len(got)} decisions for this index", False))
            continue
        actual_desc, passed = _grade_decision(want, got[0])
        fields.append(FieldResult(field_name, label, expected_desc, actual_desc, passed))
    return _finish(scenario.name, scenario.source, fields)


def _grade_decision(want, decision):
    """Returns (description of what the model decided, whether it matches `want`)."""
    got_id = decision.get("matched_topic_id")
    new_topic = decision.get("new_topic")
    if got_id:
        if new_topic:
            return f"{got_id} (new_topic also set)", False
        return got_id, got_id == want
    if _is_complete_new_topic(new_topic):
        return f"NEW: {new_topic['name']}", want is None
    return f"invalid decision {decision!r}", False


def _is_complete_new_topic(new_topic):
    # match.apply_matches needs all three keys to create the topic.
    return isinstance(new_topic, dict) and all(
        isinstance(new_topic.get(key), str) and new_topic[key].strip() for key in ("name", "slug", "description")
    )
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_evals_harness.py -v`
Expected: all PASS.

- [ ] **Step 5: Full suite + lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add evals/harness.py tests/test_evals_harness.py
git commit -m "feat(evals): grade matching scenarios per candidate decision

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Aggregate results and format the console report

**Files:**
- Modify: `evals/harness.py`
- Test: `tests/test_evals_harness.py` (append)

**Interfaces:**
- Consumes: `FieldResult`, `ExampleResult` (Task 4).
- Produces:
  - `@dataclass Report(total: int, passed: int, failed: int, errored: int, field_accuracy: dict[str, tuple[int, int]], signal_type_confusion: Counter, judge_passed: int, judge_total: int, failures: list[ExampleResult], errors: list[ExampleResult])`. `field_accuracy` maps a field to `(correct, graded)` and excludes errored examples. `signal_type_confusion` maps `(expected, actual)` to a count, for misses only.
  - `harness.aggregate(results: list[ExampleResult]) -> Report`
  - `harness.format_report(report: Report) -> str`, plain ASCII layout.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evals_harness.py`:

```python
# --- aggregation & report ---

def fr(field, passed, expected="x", actual="x", detail=None):
    return harness.FieldResult(field, field, expected, actual, passed, detail)


RESULTS = [
    harness.ExampleResult("db-001", "database_development", "passed", [
        fr("skip", True), fr("signal_type", True, "reliability_issue", "reliability_issue"), fr("summary", True),
    ]),
    harness.ExampleResult("db-002", "database_development", "failed", [
        fr("skip", True), fr("signal_type", False, "reliability_issue", "usability_issue"),
        fr("summary", False, "ref", "act", detail="Invents a cause."),
    ]),
    harness.ExampleResult("db-003", "database_development", "failed", [
        fr("skip", True), fr("signal_type", False, "reliability_issue", "usability_issue"), fr("summary", True),
    ]),
    harness.ExampleResult("db-004", "database_development", "errored", [fr("skip", False)],
                          error="API call failed: 529"),
    harness.ExampleResult("db-005", "database_development", "failed", [], error="non-JSON response"),
]


def test_aggregate_counts_and_accuracy_excludes_errored():
    report = harness.aggregate(RESULTS)

    assert (report.total, report.passed, report.failed, report.errored) == (5, 1, 3, 1)
    assert report.field_accuracy == {"skip": (3, 3), "signal_type": (1, 3), "summary": (2, 3)}
    assert report.signal_type_confusion == {("reliability_issue", "usability_issue"): 2}
    assert (report.judge_passed, report.judge_total) == (2, 3)
    assert [r.example_id for r in report.failures] == ["db-002", "db-003", "db-005"]
    assert [r.example_id for r in report.errors] == ["db-004"]


def test_format_report_shows_every_section():
    text = harness.format_report(harness.aggregate(RESULTS))

    assert "5 total, 1 passed, 3 failed, 1 errored" in text
    assert "signal_type" in text and "1/3" in text
    assert "Summary judge pass rate: 2/3 (66.7%)" in text
    assert "reliability_issue -> usability_issue  x2" in text
    assert "[database_development] db-002" in text
    assert "signal_type: expected 'reliability_issue', got 'usability_issue'" in text
    assert "-- judge: Invents a cause." in text
    assert "error: non-JSON response" in text
    assert "db-004: API call failed: 529" in text
    text.encode("ascii")  # layout itself adds no non-ASCII characters


def test_aggregate_and_format_handle_empty_and_all_errored_runs():
    for results in ([], [harness.ExampleResult("x", "s", "errored", error="boom")]):
        report = harness.aggregate(results)
        assert report.field_accuracy == {}
        text = harness.format_report(report)
        assert "Examples:" in text
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_harness.py -v -k "aggregate or format_report"`
Expected: FAIL with `AttributeError: module 'evals.harness' has no attribute 'aggregate'`.

- [ ] **Step 3: Implement in `evals/harness.py`**

Add `from collections import Counter` to the imports (after `import datetime`). Add after the `ExampleResult` dataclass:

```python
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
```

Append at the end of the file:

```python
def aggregate(results):
    field_accuracy = {}
    confusion = Counter()
    judge_passed = judge_total = 0
    for result in results:
        if result.status == "errored":
            continue
        for f in result.fields:
            correct, graded = field_accuracy.get(f.field, (0, 0))
            field_accuracy[f.field] = (correct + int(f.passed), graded + 1)
            if f.field == "signal_type" and not f.passed:
                confusion[(f.expected, f.actual)] += 1
            if f.field == "summary":
                judge_total += 1
                judge_passed += int(f.passed)

    return Report(
        total=len(results),
        passed=sum(r.status == "passed" for r in results),
        failed=sum(r.status == "failed" for r in results),
        errored=sum(r.status == "errored" for r in results),
        field_accuracy=field_accuracy,
        signal_type_confusion=confusion,
        judge_passed=judge_passed,
        judge_total=judge_total,
        failures=[r for r in results if r.status == "failed"],
        errors=[r for r in results if r.status == "errored"],
    )


def format_report(report):
    lines = [
        "== Eval report ==",
        f"Examples: {report.total} total, {report.passed} passed, {report.failed} failed, {report.errored} errored",
    ]

    if report.field_accuracy:
        lines += ["", "Per-field accuracy (errored examples excluded):"]
        for name, (correct, graded) in report.field_accuracy.items():
            lines.append(f"  {name:<16} {correct}/{graded}  {_percent(correct, graded)}")

    if report.judge_total:
        lines += ["", f"Summary judge pass rate: {report.judge_passed}/{report.judge_total} "
                      f"({_percent(report.judge_passed, report.judge_total)})"]

    if report.signal_type_confusion:
        lines += ["", "signal_type confusion (expected -> actual):"]
        for (expected, actual), count in report.signal_type_confusion.most_common():
            lines.append(f"  {expected} -> {actual}  x{count}")

    if report.failures:
        lines += ["", "Failures:"]
        for result in report.failures:
            lines.append(f"  [{result.source}] {result.example_id}")
            if result.error:
                lines.append(f"    error: {result.error}")
            for f in result.fields:
                if not f.passed:
                    line = f"    {f.label}: expected {f.expected!r}, got {f.actual!r}"
                    if f.detail:
                        line += f" -- judge: {f.detail}"
                    lines.append(line)

    if report.errors:
        lines += ["", "Errored (API failures, not counted as model mistakes):"]
        for result in report.errors:
            lines.append(f"  [{result.source}] {result.example_id}: {result.error}")

    return "\n".join(lines)


def _percent(numerator, denominator):
    return f"{100 * numerator / denominator:.1f}%" if denominator else "n/a"
```

- [ ] **Step 4: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_evals_harness.py -v`
Expected: all PASS.

- [ ] **Step 5: Full suite + lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 6: Commit**

```bash
git add evals/harness.py tests/test_evals_harness.py
git commit -m "feat(evals): aggregate results into a console report

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: CLI runners, seed golden set, docs

**Files:**
- Create: `evals/run_extraction_eval.py`, `evals/run_matching_eval.py`
- Create: `evals/golden/extraction/database_development.yaml`
- Create: `evals/golden/matching/connection_pooling_duplicate.yaml`, `evals/golden/matching/mixed_new_and_existing.yaml`
- Test: `tests/test_evals_golden.py`
- Modify: `README.md` (append an "Evals" section)

**Interfaces:**
- Consumes: `harness.load_golden_dir`, `harness.GoldenSchemaError`, `harness.filter_examples`, `harness.grade_extraction_example`, `harness.grade_matching_scenario`, `harness.aggregate`, `harness.format_report` (Tasks 2 and 4-6). `judge.JUDGE_MODEL` (Task 3). `extract.EXTRACTION_MODEL`, `match.MATCH_MODEL`. `credentials.get_secret`, `credentials.MissingCredentialError`.
- Produces: `main(argv=None) -> int` in each runner (exit code: 0 after a report, 2 for malformed golden data, an empty filter, or missing credentials).

- [ ] **Step 1: Write the failing golden-data test**

Create `tests/test_evals_golden.py`:

```python
from pathlib import Path

from evals import harness

GOLDEN_DIR = Path(__file__).resolve().parent.parent / "evals" / "golden"


def test_committed_extraction_golden_set_is_valid():
    examples = harness.load_golden_dir(GOLDEN_DIR / "extraction", "extraction")

    assert any(e.expected["skip"] for e in examples)
    assert any(not e.expected["skip"] for e in examples)


def test_committed_matching_golden_set_is_valid():
    scenarios = harness.load_golden_dir(GOLDEN_DIR / "matching", "matching")

    assert {s.name for s in scenarios} >= {"connection_pooling_duplicate", "mixed_new_and_existing"}
```

Run: `python -m pytest tests/test_evals_golden.py -v`
Expected: FAIL with `FileNotFoundError` (no `evals/golden/...` directory yet).

- [ ] **Step 2: Create the seed golden files**

`evals/golden/extraction/database_development.yaml`:

```yaml
# Seed examples: synthetic posts written to exercise every grading path (skip, signal_type,
# entity, effective_date, summary). Replace or extend with ~20 real r/ posts whose labels have
# been manually verified -- see the spec's "Open items".
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

  - id: db-003
    evidence:
      title: "Autocomplete should know about CTE columns"
      content: >
        When I write a WITH clause, the editor's autocomplete has no idea what columns
        the CTE returns, so I end up typing everything by hand. Would be a huge time saver
        if it could infer them.
    expected:
      skip: false
      signal_type: new_feature_demand
      entity: null
      effective_date: null
    reference_summary: >
      User wants the SQL editor's autocomplete to suggest columns defined in CTEs.
    notes: "Feature request, not a bug: autocomplete works, it just lacks CTE support."

  - id: db-004
    evidence:
      title: "License price doubled at renewal"
      content: >
        Our team renewal quote came in at twice last year's price for the same five seats.
        Nothing new that we use was added. Hard to justify this to finance.
    expected:
      skip: false
      signal_type: pricing_complaint
      entity: null
      effective_date: null
    reference_summary: >
      User complains the renewal price doubled for the same seats without added value.
    notes: "No switching intent stated, so pricing_complaint rather than switching_intent."

  - id: db-005
    evidence:
      title: "Oracle 19c extended support ends 2027-04-30 -- planning our move"
      content: >
        Oracle confirmed Extended Support for Database 19c ends on 2027-04-30. We have to
        migrate about 30 schemas before then and are evaluating which tooling can handle
        the schema compare and data move.
    expected:
      skip: false
      signal_type: customer_migration_intent
      entity:
        company: Oracle
      effective_date: 2027-04-30
    reference_summary: >
      User must migrate ~30 schemas off Oracle Database 19c before extended support ends
      on 2027-04-30 and is evaluating migration tooling.
    notes: >
      Exercises entity + effective_date. Only company is graded; the model's free-form
      entity.type and exact product wording are not.

  - id: db-006
    evidence:
      title: "hi"
      content: "."
    expected:
      skip: true
    notes: "Low-effort post with no content; should be skipped."
```

`evals/golden/matching/connection_pooling_duplicate.yaml`:

```yaml
name: connection_pooling_duplicate
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

`evals/golden/matching/mixed_new_and_existing.yaml`:

```yaml
name: mixed_new_and_existing
existing_topics:
  - topic_id: TOPIC-0001
    name: "Migration timeouts on large tables"
    description: "Schema/data migrations hang or time out on tables with tens of millions of rows"
  - topic_id: TOPIC-0002
    name: "CTE-aware autocomplete"
    description: "Users want editor autocomplete to suggest columns defined in WITH clauses"
candidates:
  - signal_type: reliability_issue
    summary: "Migration job stalls forever on a 60M-row table"
  - signal_type: reliability_issue
    summary: "Editor crashes when opening a 200 MB SQL file"
  - signal_type: new_feature_demand
    summary: "Wants a dark theme for the query editor"
expected:
  - index: 0
    matched_topic_id: TOPIC-0001
  - index: 1
    matched_topic_id: null
    new_topic:
      name: "Editor crash on large SQL files"
  - index: 2
    matched_topic_id: null
    new_topic:
      name: "Dark theme"
notes: >
  Candidate 1 shares signal_type with TOPIC-0001 but is a different issue -- exercises the
  prompt's "not just the same signal_type" rule. Candidate 2 shares signal_type with TOPIC-0002
  but is a different request. Golden new_topic names are documentation only; grading checks
  that a new topic was created, not its wording.
```

Run: `python -m pytest tests/test_evals_golden.py -v`
Expected: PASS.

- [ ] **Step 3: Create `evals/run_extraction_eval.py`**

```python
"""Run the extraction golden set against the real extraction prompt and model.

Usage: python evals/run_extraction_eval.py [--filter database_development] [--model MODEL]

Calls the real Anthropic API (one extraction call plus one judge call per non-skipped
example). This is a manual tool, not part of pytest or CI.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import extract  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, judge  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "extraction")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grade extract.extract_topic against the golden set.")
    parser.add_argument("--filter", help="only run the golden file with this basename, e.g. database_development")
    parser.add_argument("--model", help=f"override extract.EXTRACTION_MODEL (default: {extract.EXTRACTION_MODEL})")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        examples = harness.load_golden_dir(GOLDEN_DIR, "extraction")
    except harness.GoldenSchemaError as e:
        print(f"golden set is malformed: {e}", file=sys.stderr)
        return 2

    selected = harness.filter_examples(examples, args.filter)
    if not selected:
        available = ", ".join(sorted({e.source for e in examples}))
        print(f"no golden file named {args.filter!r}; available: {available}", file=sys.stderr)
        return 2

    if args.model:
        extract.EXTRACTION_MODEL = args.model

    try:
        client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    except MissingCredentialError as e:
        print(e, file=sys.stderr)
        return 2

    print(f"extraction model: {extract.EXTRACTION_MODEL}, judge model: {judge.JUDGE_MODEL}")
    results = []
    for number, example in enumerate(selected, 1):
        result = harness.grade_extraction_example(example, client)
        print(f"[{number}/{len(selected)}] {example.source}/{example.id}: {result.status}")
        results.append(result)

    print()
    print(harness.format_report(harness.aggregate(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create `evals/run_matching_eval.py`**

```python
"""Run the matching golden set against the real matching prompt and model.

Usage: python evals/run_matching_eval.py [--filter connection_pooling_duplicate] [--model MODEL]

Calls the real Anthropic API (one matcher call per scenario). This is a manual tool, not
part of pytest or CI.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import match  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "matching")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grade match.call_matcher against the golden scenarios.")
    parser.add_argument("--filter", help="only run the scenario with this name, e.g. connection_pooling_duplicate")
    parser.add_argument("--model", help=f"override match.MATCH_MODEL (default: {match.MATCH_MODEL})")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        scenarios = harness.load_golden_dir(GOLDEN_DIR, "matching")
    except harness.GoldenSchemaError as e:
        print(f"golden set is malformed: {e}", file=sys.stderr)
        return 2

    selected = harness.filter_examples(scenarios, args.filter)
    if not selected:
        available = ", ".join(sorted(s.name for s in scenarios))
        print(f"no scenario named {args.filter!r}; available: {available}", file=sys.stderr)
        return 2

    if args.model:
        match.MATCH_MODEL = args.model

    try:
        client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    except MissingCredentialError as e:
        print(e, file=sys.stderr)
        return 2

    print(f"matching model: {match.MATCH_MODEL}")
    results = []
    for number, scenario in enumerate(selected, 1):
        result = harness.grade_matching_scenario(scenario, client)
        print(f"[{number}/{len(selected)}] {scenario.name}: {result.status}")
        results.append(result)

    print()
    print(harness.format_report(harness.aggregate(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Verify the runners without touching the API**

Run each of the following and check the result:

```bash
python evals/run_extraction_eval.py --help
python evals/run_matching_eval.py --help
python evals/run_extraction_eval.py --filter typo; echo "exit=$?"
python evals/run_matching_eval.py --filter typo; echo "exit=$?"
```

Expected: both `--help` commands print usage with `--filter` and `--model`. Both `typo` runs print `no golden file named 'typo'; available: database_development` (or `no scenario named 'typo'; available: connection_pooling_duplicate, mixed_new_and_existing`) and `exit=2`. No credential prompt and no API call happen.

Then check the malformed-data path: temporarily change `signal_type: pricing_complaint` to `signal_type: pricing` in `database_development.yaml` and run `python evals/run_extraction_eval.py; echo "exit=$?"`. Expected: `golden set is malformed: database_development.yaml [db-004]: invalid expected signal_type 'pricing'` and `exit=2`. **Revert the change** (`git checkout evals/golden/extraction/database_development.yaml` after the first commit, or undo the edit by hand before committing).

- [ ] **Step 6: Append the Evals section to `README.md`**

Append at the end of `README.md`:

````markdown
## Evals

`evals/` holds a golden-set eval suite for the two stages that ask Claude to make judgment
calls: extraction (`extract.py`) and topic matching (`match.py`). The runners call the real
production functions against the real Anthropic API. They are **not** run by pytest or CI.

```powershell
python evals/run_extraction_eval.py [--filter database_development] [--model claude-sonnet-5]
python evals/run_matching_eval.py   [--filter connection_pooling_duplicate] [--model claude-sonnet-5]
```

- The API key comes from the OS credential store, the same way the pipeline gets it (`set_credentials.py`).
- The extraction eval exact-matches `skip`, `signal_type`, `entity` (only the keys the golden file names, case-insensitive), and `effective_date`. It grades `summary` with an LLM judge (`evals/judge.py`, `claude-opus-5`). Each non-skipped example costs two API calls.
- The matching eval checks each candidate's decision: it must match the named existing topic, or create a complete new topic when the golden file says `matched_topic_id: null`.
- Results are classified as `passed`, `failed` (a model mistake, including unusable output), or `errored` (the API call failed, so the example is excluded from accuracy). The exit code is 0 whenever a report is printed. It is 2 for malformed golden YAML, a `--filter` that matches nothing, or missing credentials.
- Golden data lives in `evals/golden/extraction/<product_area>.yaml` (many examples per file) and `evals/golden/matching/<scenario>.yaml` (one scenario per file). See the design spec at `docs/superpowers/specs/2026-09-24-extraction-matching-evals-design.md` for the format. `tests/test_evals_golden.py` validates the committed golden files in CI.
````

- [ ] **Step 7: Full suite + lint**

Run: `python -m pytest -q && ruff check .`
Expected: all pass, no lint errors. Also confirm pytest did not collect anything under `evals/`: `python -m pytest --collect-only -q | grep -c "^evals/"` prints `0`.

- [ ] **Step 8: Commit**

```bash
git add evals/run_extraction_eval.py evals/run_matching_eval.py evals/golden tests/test_evals_golden.py README.md
git commit -m "feat(evals): CLI runners, seed golden set, and README docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 9 (optional, costs API credits, needs the key in the credential store): live smoke run**

Run: `python evals/run_matching_eval.py --filter connection_pooling_duplicate`
Expected: a single `[1/1] connection_pooling_duplicate: passed|failed` line followed by the report. `errored` with a no-text-block message would mean Task 1 regressed. Only run this if the user has approved spending API credits.
