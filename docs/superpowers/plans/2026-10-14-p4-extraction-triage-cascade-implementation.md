# P4: Extraction Triage Cascade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut extraction cost on obvious noise without losing real signals. Two levers:

1. Measure the cheapest option first: the existing extractor at a lower `effort`.
2. Add an optional Claude Haiku 5.5 triage call that can only *skip* posts. Its false-skip rate is measured on the golden set before launch, and on a production audit sample after.

**Architecture:**
- `extract.EXTRACTION_EFFORT` makes Sonnet's effort configurable. Eval runs record it as a setting, and comparisons warn when it changes.
- A new `triage.py` makes one narrow call (route `skip` or `extract`, plus a confidence). A skip is trusted only at `confidence >= TRIAGE_SKIP_THRESHOLD`. A failed triage call falls through to the extractor.
- `extract.extract_one(..., cascade=True)` runs triage through the same ledger as every other model call. A deterministic 5% sample of trusted skips still goes to the extractor, and `query.py triage-audit` reports where the two disagree.
- An eval runner sweeps skip thresholds on the golden set.
- Everything is off unless `config.yaml` sets `extraction_cascade: true`.

**Tech Stack:** Python 3.11, Anthropic SDK 0.122, sqlite3 (JSON1), pytest, ruff.

**Spec:** [docs/superpowers/specs/2026-10-14-durability-and-quality-roadmap-design.md](../specs/2026-10-14-durability-and-quality-roadmap-design.md), sections 1.5 (C1), 4.8 (models and the cascade guardrail), 5 "P4". **Requires P1** (ledger, `extract_one`, `evidence_extraction.decided_by`) **and P3 Task 1** (token and confidence capture in eval results).

## Global Constraints

- Everything from P1's Global Constraints still applies.
- Model IDs, verbatim: triage `claude-haiku-5-5`, extraction stays `claude-sonnet-5`. Per the Claude API reference:
  - Haiku 5.5 runs adaptive thinking by default and defaults to `effort: medium`, so triage sets `output_config={"effort": "low"}` explicitly.
  - Effort goes inside `output_config`, never top level.
  - `budget_tokens` is rejected; never send it.
- Triage can only skip. It never sets `signal_type`, `summary` or `confidence` on a candidate.
- **Fail open.** Any triage failure (API error, unparseable response, low confidence) sends the post to the extractor.
- `extraction_cascade` defaults to `false`. No task turns it on; Task 5 is the owner's decision.
- No schema migration in this plan.

## Review Focus

1. **Haiku is down or returns garbage for a whole run.** Expected: every post still goes to the extractor; the ledger shows the triage failures; no post is skipped. Pinned in Task 4 (`test_triage_failure_falls_through_to_extraction`).
2. **A real complaint that Haiku confidently calls spam.** Expected: measured before launch (the Task 3 sweep lists false-skip ids per threshold), and after launch (`query.py triage-audit` on the 5% audit sample). Pinned in Task 3 and Task 4.
3. **The same post retried after a failed extraction.** Expected: its audit-sample membership is the same on every attempt (a deterministic hash, not `random`). Pinned in Task 4 (`test_audit_sample_is_deterministic_and_close_to_the_rate`).
4. **Comparing an eval run at `effort: low` with one at the default.** Expected: the comparison warns `extraction_effort setting changed`. Pinned in Task 1.
5. **Old config files** without `extraction_cascade`. Expected: they load with the cascade off. Pinned in Task 4 (`tests/test_config.py`).

---

## File Structure

| File | Responsibility |
|---|---|
| `extract.py` (modify) | `EXTRACTION_EFFORT`; triage step in `extract_one`; audit sampling; `run(..., cascade=)` |
| `triage.py` (create) | Triage prompt, request, parse, `should_skip` |
| `evals/runs.py` (modify) | `settings` in run records and comparison warnings; `KINDS` adds `triage` |
| `evals/run_extraction_eval.py` (modify) | `--effort` |
| `evals/triage_eval.py` (create) | `grade_triage_example`, `triage_sweep`, `format_triage_sweep` |
| `evals/run_triage_eval.py` (create) | CLI: triage on the extraction golden set, threshold sweep |
| `config.py`, `config.yaml.example`, `run_weekly.py` (modify) | `extraction_cascade` flag |
| `db.py`, `query.py` (modify) | `triage_audit` read and `triage-audit` command |
| `docs/decisions/007-triage-before-extraction.md` (create) | ADR with measurements |
| `README.md` (modify) | Cost section, flag, audit |
| `tests/test_triage.py`, `tests/test_evals_triage.py` (create); `tests/test_extract.py`, `tests/test_evals_runs.py`, `tests/test_evals_runners.py`, `tests/test_config.py`, `tests/test_run_weekly.py`, `tests/test_query.py` (modify) | Tests |

**Branching:** `git checkout main && git pull && git checkout -b feat/extraction-triage`.

---

### Task 1: Configurable extraction effort, recorded as a run setting

**Files:**
- Modify: `extract.py` (`EXTRACTION_EFFORT`, `request_extraction`)
- Modify: `evals/runs.py` (`build_run_record(..., settings=None)`, `compare_runs` warnings)
- Modify: `evals/run_extraction_eval.py` (`--effort`)
- Test: `tests/test_extract.py`, `tests/test_evals_runs.py`, `tests/test_evals_runners.py` (append)

**Interfaces:**
- Consumes: `extract.request_extraction` (P1); `runs.build_run_record` (P3 Task 1 shape).
- Produces:
  - `extract.EXTRACTION_EFFORT: str | None = None` (`None` means the model default)
  - `extract.EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")`
  - `runs.build_run_record(kind, results, report, *, models, prompts, golden, filter_name, started_at, commit, settings=None)`; the record gains `"settings": dict`
  - `compare_runs` warns `"<name> setting changed: <before> -> <after>"`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extract.py`:

```python
class CapturingMessages:
    def __init__(self, text):
        self.text, self.kwargs = text, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeResponse(self.text)


def test_request_extraction_sends_effort_only_when_configured(monkeypatch):
    client = FakeClient([])
    client.messages = CapturingMessages(VALID)

    extract.request_extraction(client, SAMPLE_EVIDENCE)
    assert "output_config" not in client.messages.kwargs

    monkeypatch.setattr(extract, "EXTRACTION_EFFORT", "low")
    extract.request_extraction(client, SAMPLE_EVIDENCE)
    assert client.messages.kwargs["output_config"] == {"effort": "low"}
```

Append to `tests/test_evals_runs.py`:

```python
def test_compare_runs_warns_when_a_setting_changes():
    baseline = make_record()
    current = make_record()
    current["settings"] = {"extraction_effort": "low"}

    comparison = runs.compare_runs(baseline, current)

    assert "extraction_effort setting changed: None -> low" in comparison.warnings


def test_build_run_record_stores_settings():
    record = runs.build_run_record(
        "extraction", RESULTS, harness.aggregate(RESULTS), models={}, prompts={}, golden="g",
        filter_name=None, started_at=STARTED, commit=None, settings={"extraction_effort": "medium"},
    )
    assert record["settings"] == {"extraction_effort": "medium"}
```

(`RESULTS` and `STARTED` are the module-level fixtures `make_record` already uses. If they have different names in that file, use those.)

Append to `tests/test_evals_runners.py`:

```python
def test_extraction_runner_effort_flag_is_applied_and_recorded(tmp_path, monkeypatch):
    seen = []

    class Capturing(RepeatingMessages):
        def create(self, **kwargs):
            seen.append(kwargs.get("output_config"))
            return super().create(**kwargs)

    client = RepeatingClient(json.dumps({"skip": True}))
    client.messages = Capturing(json.dumps({"skip": True}))
    monkeypatch.setattr(extract, "EXTRACTION_EFFORT", None)

    code = run_extraction_eval.main(["--filter", "database_development", "--effort", "low",
                                     "--results-dir", str(tmp_path)], client=client)

    assert code == 0
    assert {"effort": "low"} in seen
    [(path, record)], _ = runs.find_runs(tmp_path, "extraction")
    assert record["settings"] == {"extraction_effort": "low"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_extract.py tests/test_evals_runs.py tests/test_evals_runners.py -v`
Expected: FAIL (no `EXTRACTION_EFFORT`, no `settings`, `unrecognized arguments: --effort`).

- [ ] **Step 3: Implement**

`extract.py`, after `EXTRACTION_MODEL`:

```python
EXTRACTION_EFFORT = None  # None = the model's default effort (high on Sonnet 5)
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
```

In `request_extraction`, build the request so effort is sent only when set:

```python
def request_extraction(client, evidence):
    """Make the extraction API call. Returns an LLMReply; raises ExtractionAPIError if the call itself fails."""
    request = {
        "model": EXTRACTION_MODEL,
        "max_tokens": 4000,
        "messages": [{"role": "user", "content": build_extraction_prompt(evidence)}],
    }
    if EXTRACTION_EFFORT:
        request["output_config"] = {"effort": EXTRACTION_EFFORT}
    try:
        response = client.messages.create(**request)
    except Exception as e:
        raise ExtractionAPIError(f"evidence {_label(evidence)}: API call failed: {e}") from e
    return reply_from_response(response)
```

`evals/runs.py`: add a `settings=None` keyword to `build_run_record`, and add `"settings": dict(settings or {}),` to the record right after `"prompts"`. In `compare_runs`, extend the warning loop:

```python
    for key, noun in (("models", "model"), ("prompts", "prompt"), ("settings", "setting")):
```

`evals/run_extraction_eval.py`: add the argument and apply it next to `--model`:

```python
    parser.add_argument("--effort", choices=extract.EFFORT_LEVELS,
                        help="override extract.EXTRACTION_EFFORT (default: the model's own default)")
    ...
    if args.effort:
        extract.EXTRACTION_EFFORT = args.effort
```

Pass the setting to the record:

```python
            settings={"extraction_effort": extract.EXTRACTION_EFFORT},
```

The header line printed at the start becomes `print(f"extraction model: {extract.EXTRACTION_MODEL} (effort: {extract.EXTRACTION_EFFORT or 'default'}), judge model: {judge.JUDGE_MODEL}")`.

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_extract.py tests/test_evals_runs.py tests/test_evals_runners.py -v`
Expected: PASS.

- [ ] **Step 5: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add extract.py evals/runs.py evals/run_extraction_eval.py tests/test_extract.py tests/test_evals_runs.py tests/test_evals_runners.py
git commit -m "feat(extract): configurable extraction effort, recorded and compared as an eval setting"
```

- [ ] **Step 6: Measure the cheaper-effort baseline (manual, costs API calls)**

```powershell
python evals/run_extraction_eval.py
python evals/run_extraction_eval.py --effort medium
python evals/run_extraction_eval.py --effort low
python evals/compare_runs.py evals/results/extraction-<default>.json evals/results/extraction-<low>.json
```

For each effort, write down in the PR:

- pass rate;
- per-field accuracy (especially `skip` and `signal_type`);
- calibration error;
- `Extraction tokens: X in / Y out`.

This is the bar the cascade has to beat (spec §4.8).

---

### Task 2: The triage call

**Files:**
- Create: `triage.py`
- Test: `tests/test_triage.py` (create)

**Interfaces:**
- Consumes: `llm.reply_from_response` (P1).
- Produces:
  - Constants: `triage.TRIAGE_MODEL = "claude-haiku-5-5"`, `TRIAGE_EFFORT = "low"`, `TRIAGE_MAX_TOKENS = 2048`, `TRIAGE_SKIP_THRESHOLD = 0.9`, `TRIAGE_PROMPT_TEMPLATE`, `TRIAGE_ROUTES = ("skip", "extract")`
  - Errors: `TriageError`, `TriageAPIError`, `TriageResponseError`
  - `triage.TriageDecision(route: str, confidence: float, reason: str)` (frozen)
  - `triage.build_triage_prompt(evidence) -> str`
  - `triage.request_triage(client, evidence) -> LLMReply`
  - `triage.parse_triage_reply(reply, evidence_label) -> TriageDecision`
  - `triage.triage(client, evidence) -> TriageDecision`
  - `triage.should_skip(decision, threshold=TRIAGE_SKIP_THRESHOLD) -> bool`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_triage.py`:

```python
import json

import pytest

import triage
from llm import LLMReply


class Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class Response:
    stop_reason = "end_turn"

    def __init__(self, text):
        self.content = [Text(text)]


class Messages:
    def __init__(self, result):
        self.result, self.kwargs = result, None

    def create(self, **kwargs):
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return Response(self.result)


class Client:
    def __init__(self, result):
        self.messages = Messages(result)


EVIDENCE = {"evidence_id": "EV-2026-000001", "title": "Buy followers cheap!!!", "content": "DM me"}


def reply(text):
    return LLMReply(text=text, stop_reason="end_turn", input_tokens=None, output_tokens=None)


def test_request_uses_haiku_at_low_effort_and_the_post_text():
    client = Client(json.dumps({"route": "skip", "confidence": 0.97, "reason": "spam"}))

    decision = triage.triage(client, EVIDENCE)

    assert decision == triage.TriageDecision("skip", 0.97, "spam")
    sent = client.messages.kwargs
    assert sent["model"] == "claude-haiku-5-5"
    assert sent["output_config"] == {"effort": "low"}
    assert "thinking" not in sent and "budget_tokens" not in json.dumps(sent)
    assert "Buy followers cheap!!!" in sent["messages"][0]["content"]


def test_api_failure_is_a_triage_api_error():
    with pytest.raises(triage.TriageAPIError):
        triage.triage(Client(RuntimeError("529")), EVIDENCE)


@pytest.mark.parametrize("text", [
    "not json", "[]", json.dumps({"route": "maybe", "confidence": 0.5}),
    json.dumps({"route": "skip", "confidence": "high"}), json.dumps({"route": "skip", "confidence": 1.5}),
    json.dumps({"route": "skip"}),
])
def test_unusable_replies_are_response_errors(text):
    with pytest.raises(triage.TriageResponseError):
        triage.parse_triage_reply(reply(text), "EV-x")


def test_missing_text_block_is_a_response_error():
    with pytest.raises(triage.TriageResponseError, match="no text block"):
        triage.parse_triage_reply(LLMReply(None, "max_tokens", None, None), "EV-x")


def test_reason_is_optional():
    decision = triage.parse_triage_reply(reply(json.dumps({"route": "extract", "confidence": 0.4})), "EV-x")
    assert decision.reason == ""


@pytest.mark.parametrize("route,confidence,skip", [
    ("skip", 0.95, True), ("skip", 0.9, True), ("skip", 0.89, False), ("extract", 0.99, False),
])
def test_should_skip_only_trusts_confident_skips(route, confidence, skip):
    assert triage.should_skip(triage.TriageDecision(route, confidence, "")) is skip
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_triage.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'triage'`).

- [ ] **Step 3: Create `triage.py`**

```python
"""Cheap pre-filter in front of the extractor. It decides only whether a post is worth extracting.

Triage never classifies and never writes a candidate; it can only skip. A skip is trusted when
the model says "skip" with confidence >= TRIAGE_SKIP_THRESHOLD. Anything else, including any
failure, sends the post to the extractor (fail open). See ADR 007.
"""
import json
import math
from dataclasses import dataclass

from llm import reply_from_response

TRIAGE_MODEL = "claude-haiku-5-5"
TRIAGE_EFFORT = "low"  # Haiku 5.5 defaults to medium; triage is a quick classification
TRIAGE_MAX_TOKENS = 2048
TRIAGE_SKIP_THRESHOLD = 0.9
TRIAGE_ROUTES = ("skip", "extract")

TRIAGE_PROMPT_TEMPLATE = """You are screening Reddit posts before a more expensive product-feedback extractor reads them.

Post title: {title}
Post body: {content}

Decide whether this post could contain product feedback about a software product: a complaint, \
a feature request, a usability or reliability problem, a pricing concern, a comparison with a \
competitor, an intent to switch, or a description of how someone uses the product.

Respond with ONLY a JSON object with these exact keys:
- "route": "skip" if the post is clearly not product feedback (spam, self-promotion, off-topic, \
empty, or AI-generated filler), otherwise "extract"
- "confidence": a number between 0.0 and 1.0 for how sure you are of the route
- "reason": a short phrase

When in doubt, choose "extract". Skipping real feedback is much worse than extracting noise.
"""


class TriageError(Exception):
    pass


class TriageAPIError(TriageError):
    """The API call itself failed."""


class TriageResponseError(TriageError):
    """The model responded, but the response was unusable."""


@dataclass(frozen=True)
class TriageDecision:
    route: str
    confidence: float
    reason: str


def build_triage_prompt(evidence):
    return TRIAGE_PROMPT_TEMPLATE.format(title=evidence["title"], content=evidence["content"])


def request_triage(client, evidence):
    try:
        response = client.messages.create(
            model=TRIAGE_MODEL,
            max_tokens=TRIAGE_MAX_TOKENS,
            output_config={"effort": TRIAGE_EFFORT},
            messages=[{"role": "user", "content": build_triage_prompt(evidence)}],
        )
    except Exception as e:
        raise TriageAPIError(f"evidence {evidence.get('evidence_id', '<no evidence_id>')}: triage call failed: {e}") from e
    return reply_from_response(response)


def parse_triage_reply(reply, evidence_label):
    if reply.text is None:
        raise TriageResponseError(f"evidence {evidence_label}: triage response has no text block "
                                  f"(stop_reason={reply.stop_reason!r})")
    try:
        parsed = json.loads(reply.text)
    except json.JSONDecodeError as e:
        raise TriageResponseError(f"evidence {evidence_label}: non-JSON triage response: {reply.text!r}") from e
    if not isinstance(parsed, dict):
        raise TriageResponseError(f"evidence {evidence_label}: expected a JSON object, got {reply.text!r}")

    route = parsed.get("route")
    if route not in TRIAGE_ROUTES:
        raise TriageResponseError(f"evidence {evidence_label}: invalid route {route!r}")
    confidence = parsed.get("confidence")
    if (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0):
        raise TriageResponseError(f"evidence {evidence_label}: invalid confidence {confidence!r}")
    reason = parsed.get("reason")
    return TriageDecision(route, float(confidence), reason.strip() if isinstance(reason, str) else "")


def triage(client, evidence):
    return parse_triage_reply(request_triage(client, evidence), evidence.get("evidence_id", "<no evidence_id>"))


def should_skip(decision, threshold=TRIAGE_SKIP_THRESHOLD):
    return decision.route == "skip" and decision.confidence >= threshold
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_triage.py -v`
Expected: PASS.

- [ ] **Step 5: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add triage.py tests/test_triage.py
git commit -m "feat: Haiku triage call that can only skip, fails open, and validates its output"
```

---

### Task 3: Triage eval with a skip-threshold sweep

**Files:**
- Create: `evals/triage_eval.py`
- Create: `evals/run_triage_eval.py`
- Modify: `evals/runs.py` (`KINDS` adds `triage`)
- Test: `tests/test_evals_triage.py` (create)

**Interfaces:**
- Consumes: `triage.*` (Task 2); `harness.ExtractionExample`, `ExampleResult`, `FieldResult`, `aggregate`, `format_report`, `load_golden_dir`, `filter_examples`; `runs.*`.
- Produces:
  - `triage_eval.SWEEP_THRESHOLDS = (0.7, 0.8, 0.9, 0.95)`
  - `triage_eval.grade_triage_example(example, client) -> ExampleResult`: one `route` field, expected `"skip"` when the golden says skip, otherwise `"extract"`; `confidence` and token usage set
  - `triage_eval.triage_sweep(results, thresholds=SWEEP_THRESHOLDS) -> list[dict]`, rows keyed `threshold`, `would_skip`, `caught_skips`, `false_skips`, `false_skip_ids`, `golden_skips`, `graded`
  - `triage_eval.format_triage_sweep(rows) -> str`
  - `run_triage_eval.main(argv=None, client=None) -> int` (`--filter`, `--model`, `--no-save`, `--results-dir`); saves kind `triage` with `record["triage_sweep"]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_evals_triage.py`:

```python
import json

from evals import harness, run_triage_eval, runs, triage_eval


class Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class Response:
    stop_reason = "end_turn"

    def __init__(self, text):
        self.content = [Text(text)]


class Repeating:
    def __init__(self, text):
        self.text = text

    def create(self, **kwargs):
        return Response(self.text)


class Client:
    def __init__(self, text):
        self.messages = Repeating(text)


def example(example_id, skip):
    expected = {"skip": True} if skip else {"skip": False, "signal_type": "reliability_issue", "entity": None,
                                            "effective_date": None}
    return harness.ExtractionExample(example_id, "s", {"title": "t", "content": "c"}, expected, None if skip else "r")


def routed(example_id, expected, actual, confidence):
    field = harness.FieldResult("route", "route", expected, actual, expected == actual)
    return harness.ExampleResult(example_id, "s", "passed" if expected == actual else "failed", [field],
                                 confidence=confidence)


def test_grade_triage_example_compares_route_with_the_golden_skip_label():
    result = triage_eval.grade_triage_example(example("e1", skip=True),
                                              Client(json.dumps({"route": "skip", "confidence": 0.93, "reason": "spam"})))
    assert (result.status, result.confidence) == ("passed", 0.93)
    assert (result.fields[0].expected, result.fields[0].actual, result.fields[0].detail) == ("skip", "skip", "spam")


def test_grade_triage_example_marks_unusable_output_failed():
    result = triage_eval.grade_triage_example(example("e1", skip=False), Client("nope"))
    assert result.status == "failed"


def test_sweep_counts_caught_and_false_skips_per_threshold():
    results = [
        routed("spam1", "skip", "skip", 0.97),
        routed("spam2", "skip", "skip", 0.85),
        routed("real1", "extract", "skip", 0.92),   # a false skip at <= 0.92
        routed("real2", "extract", "extract", 0.99),
        harness.ExampleResult("err", "s", "errored", error="529"),
    ]

    rows = {r["threshold"]: r for r in triage_eval.triage_sweep(results)}

    assert (rows[0.8]["would_skip"], rows[0.8]["caught_skips"], rows[0.8]["false_skips"]) == (3, 2, 1)
    assert rows[0.8]["false_skip_ids"] == ["real1"]
    assert (rows[0.95]["would_skip"], rows[0.95]["false_skips"]) == (1, 0)
    assert rows[0.95]["golden_skips"] == 2 and rows[0.95]["graded"] == 4


def test_format_sweep_is_ascii_and_names_false_skips():
    text = triage_eval.format_triage_sweep(triage_eval.triage_sweep([routed("real1", "extract", "skip", 0.92)]))
    assert "false skips: real1" in text
    text.encode("ascii")


def test_runner_saves_a_triage_run_with_the_sweep(tmp_path):
    client = Client(json.dumps({"route": "extract", "confidence": 0.8, "reason": "feedback"}))

    code = run_triage_eval.main(["--filter", "database_development", "--results-dir", str(tmp_path)], client=client)

    assert code == 0
    [(path, record)], _ = runs.find_runs(tmp_path, "triage")
    assert record["models"] == {"triage": "claude-haiku-5-5"}
    assert [row["threshold"] for row in record["triage_sweep"]] == list(triage_eval.SWEEP_THRESHOLDS)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_evals_triage.py -v`
Expected: FAIL (`ImportError`).

- [ ] **Step 3: Create `evals/triage_eval.py`**

```python
"""Grade the triage call against the extraction golden set's skip labels, and sweep skip thresholds."""
import triage
from evals.harness import ExampleResult, FieldResult

SWEEP_THRESHOLDS = (0.7, 0.8, 0.9, 0.95)


def grade_triage_example(example, client):
    evidence = {**example.evidence, "evidence_id": example.id}
    try:
        reply = triage.request_triage(client, evidence)
    except triage.TriageAPIError as e:
        return ExampleResult(example.id, example.source, "errored", error=str(e))
    usage = {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens}
    try:
        decision = triage.parse_triage_reply(reply, example.id)
    except triage.TriageResponseError as e:
        return ExampleResult(example.id, example.source, "failed", error=str(e), **usage)
    expected = "skip" if example.expected["skip"] else "extract"
    passed = decision.route == expected
    return ExampleResult(example.id, example.source, "passed" if passed else "failed",
                         [FieldResult("route", "route", expected, decision.route, passed, detail=decision.reason)],
                         confidence=decision.confidence, **usage)


def triage_sweep(results, thresholds=SWEEP_THRESHOLDS):
    """For each threshold: how many posts triage would skip, how many of those the golden set also
    skips (savings) and how many it would not (false skips: real signals the extractor never sees)."""
    graded = [r for r in results if r.status != "errored" and r.fields]
    golden_skips = sum(r.fields[0].expected == "skip" for r in graded)
    rows = []
    for threshold in thresholds:
        would = [r for r in graded if r.fields[0].actual == "skip" and r.confidence is not None
                 and r.confidence >= threshold]
        false = [r.example_id for r in would if r.fields[0].expected == "extract"]
        rows.append({"threshold": threshold, "would_skip": len(would), "caught_skips": len(would) - len(false),
                     "false_skips": len(false), "false_skip_ids": false, "golden_skips": golden_skips,
                     "graded": len(graded)})
    return rows


def format_triage_sweep(rows):
    lines = ["Skip-threshold sweep (triage skips the extractor only for route=skip at or above the threshold):",
             "  threshold  skipped  caught/golden skips  false skips  share of posts skipped"]
    for row in rows:
        share = f"{100 * row['would_skip'] / row['graded']:.1f}%" if row["graded"] else "n/a"
        line = (f"  {row['threshold']:<9.2f}  {row['would_skip']:<7}  "
                f"{row['caught_skips']}/{row['golden_skips']:<17}  {row['false_skips']:<11}  {share}")
        if row["false_skip_ids"]:
            line += f"  false skips: {', '.join(row['false_skip_ids'])}"
        lines.append(line)
    return "\n".join(lines)
```

- [ ] **Step 4: Create `evals/run_triage_eval.py` and extend `KINDS`**

In `evals/runs.py`: `KINDS = ("extraction", "matching", "judge", "oracle_matching", "oracle_extraction", "triage")`.

```python
"""Run the triage call over the extraction golden set and sweep skip thresholds.

Usage: python evals/run_triage_eval.py [--filter FILE] [--model MODEL] [--no-save]

One Haiku call per example. The golden "skip" label is the expected route. The sweep shows,
per threshold, the share of posts triage would skip and every false skip (a real signal the
extractor would never see). Saved as kind "triage". Not part of CI.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import triage  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, runs, triage_eval  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "extraction")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Grade triage against the extraction golden set's skip labels.")
    parser.add_argument("--filter")
    parser.add_argument("--model", help=f"override triage.TRIAGE_MODEL (default: {triage.TRIAGE_MODEL})")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR))
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        examples = harness.load_golden_dir(GOLDEN_DIR, "extraction")
    except harness.GoldenSchemaError as e:
        print(f"golden set is malformed: {e}", file=sys.stderr)
        return 2
    selected = harness.filter_examples(examples, args.filter)
    if not selected:
        print(f"no golden file named {args.filter!r}", file=sys.stderr)
        return 2
    if args.model:
        triage.TRIAGE_MODEL = args.model
    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    print(f"triage model: {triage.TRIAGE_MODEL} (effort {triage.TRIAGE_EFFORT})")
    results = []
    for number, example in enumerate(selected, 1):
        result = triage_eval.grade_triage_example(example, client)
        print(f"[{number}/{len(selected)}] {example.source}/{example.id}: {result.status}")
        results.append(result)

    report = harness.aggregate(results)
    sweep = triage_eval.triage_sweep(results)
    print()
    print(harness.format_report(report))
    print()
    print(triage_eval.format_triage_sweep(sweep))

    if not args.no_save:
        record = runs.build_run_record(
            "triage", results, report, models={"triage": triage.TRIAGE_MODEL},
            prompts={"triage": runs.fingerprint(triage.TRIAGE_PROMPT_TEMPLATE)},
            golden=runs.golden_fingerprint(selected), filter_name=args.filter, started_at=started_at,
            commit=runs.git_commit(), settings={"triage_effort": triage.TRIAGE_EFFORT},
        )
        record["triage_sweep"] = sweep
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

`format_report`'s "Extraction tokens" line reports triage tokens on this run. The label says "Extraction", which is wrong here. Change that one label in `harness.format_report` to `"Model tokens"` and update the assertion in `tests/test_evals_harness.py::test_report_shows_calibration_and_tokens_in_ascii` to match.

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_evals_triage.py tests/test_evals_harness.py tests/test_evals_runs.py -v`
Expected: PASS.

- [ ] **Step 6: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add evals/triage_eval.py evals/run_triage_eval.py evals/runs.py evals/harness.py tests/test_evals_triage.py tests/test_evals_harness.py
git commit -m "feat(evals): triage eval with a skip-threshold sweep listing every false skip"
```

---

### Task 4: The cascade in the pipeline, behind a flag, with a production audit

**Files:**
- Modify: `extract.py` (`TRIAGE_AUDIT_RATE`, `in_audit_sample`, `_triage_one`, `extract_one(..., cascade=False)`, `run(..., cascade=False)`)
- Modify: `config.py`, `config.yaml.example`, `run_weekly.py`
- Modify: `db.py` (`triage_audit`), `query.py` (`triage-audit`)
- Test: `tests/test_extract.py`, `tests/test_config.py`, `tests/test_run_weekly.py`, `tests/test_query.py` (append or update)

**Interfaces:**
- Consumes: `triage.*` (Task 2); `ledger.*`, `db.record_extraction_outcome(..., decided_by=)` (P1).
- Produces:
  - `extract.TRIAGE_AUDIT_RATE = 0.05`
  - `extract.in_audit_sample(evidence_id, rate=TRIAGE_AUDIT_RATE) -> bool`
  - `extract.extract_one(conn, client, evidence, cascade=False) -> str` (adds outcome `triaged_skip`)
  - `extract.run(db_path=..., today=None, client=None, cascade=False)`
  - Triage ledger rows: `stage="triage"`, `request_meta={"audit": bool, "threshold": float}`, `parsed={"route", "confidence", "reason"}`
  - `config.load_config(...)["extraction_cascade"]: bool` (default `False`)
  - `db.triage_audit(conn) -> dict` with keys `audited_skips`, `extractor_agreed`, `false_skips`, `pending`, `false_skip_rate`, `false_skip_evidence_ids`
  - `query.triage_audit_command(conn)`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_extract.py`:

```python
import triage as triage_module

TRIAGE_SKIP = json.dumps({"route": "skip", "confidence": 0.97, "reason": "spam"})
TRIAGE_EXTRACT = json.dumps({"route": "extract", "confidence": 0.8, "reason": "feedback"})


class ByModel:
    """Answers triage (Haiku) and extraction (Sonnet) calls from separate queues."""

    def __init__(self, triage_answers, extraction_answers):
        self.triage, self.extraction = list(triage_answers), list(extraction_answers)
        self.models = []

    def create(self, **kwargs):
        self.models.append(kwargs["model"])
        queue = self.triage if kwargs["model"] == triage_module.TRIAGE_MODEL else self.extraction
        result = queue.pop(0)
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


def by_model(triage_answers, extraction_answers):
    client = FakeClient([])
    client.messages = ByModel(triage_answers, extraction_answers)
    return client


def test_confident_triage_skip_never_reaches_the_extractor(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "in_audit_sample", lambda evidence_id, rate=0.05: False)
    db_path, [evidence_id] = _seed(tmp_path)
    client = by_model([TRIAGE_SKIP], [])

    extract.run(db_path=db_path, client=client, cascade=True)

    conn = db.connect(db_path)
    state = db.get_extraction_state(conn, evidence_id)
    assert (state["status"], state["decided_by"]) == ("skipped", "triage")
    assert client.messages.models == [triage_module.TRIAGE_MODEL]
    call = ledger.get_call(conn, state["last_call_id"])
    assert (call["stage"], call["parsed"]["route"], call["request_meta"]["audit"]) == ("triage", "skip", False)


def test_triage_extract_route_goes_on_to_the_extractor(tmp_path):
    db_path, [evidence_id] = _seed(tmp_path)

    extract.run(db_path=db_path, client=by_model([TRIAGE_EXTRACT], [VALID]), cascade=True)

    conn = db.connect(db_path)
    assert db.get_extraction_state(conn, evidence_id)["decided_by"] == "extractor"


@pytest.mark.parametrize("triage_answer", [RuntimeError("haiku down"), "not json",
                                           json.dumps({"route": "skip", "confidence": 0.5})])
def test_triage_failure_falls_through_to_extraction(tmp_path, triage_answer):
    db_path, [evidence_id] = _seed(tmp_path)

    extract.run(db_path=db_path, client=by_model([triage_answer], [VALID]), cascade=True)

    conn = db.connect(db_path)
    assert db.get_extraction_state(conn, evidence_id)["status"] == "candidate"


def test_audited_triage_skip_is_still_extracted(tmp_path, monkeypatch):
    monkeypatch.setattr(extract, "in_audit_sample", lambda evidence_id, rate=0.05: True)
    db_path, [evidence_id] = _seed(tmp_path)

    extract.run(db_path=db_path, client=by_model([TRIAGE_SKIP], [VALID]), cascade=True)

    conn = db.connect(db_path)
    assert db.get_extraction_state(conn, evidence_id)["status"] == "candidate"
    meta = conn.execute("SELECT request_meta FROM llm_call WHERE stage = 'triage'").fetchone()[0]
    assert json.loads(meta)["audit"] is True


def test_cascade_off_makes_no_triage_call(tmp_path):
    db_path, _ = _seed(tmp_path)
    client = by_model([], [VALID])

    extract.run(db_path=db_path, client=client)

    assert client.messages.models == [extract.EXTRACTION_MODEL]


def test_audit_sample_is_deterministic_and_close_to_the_rate():
    ids = [f"EV-2026-{i:06d}" for i in range(4000)]
    sampled = [i for i in ids if extract.in_audit_sample(i)]
    assert sampled == [i for i in ids if extract.in_audit_sample(i)]
    assert 0.03 < len(sampled) / len(ids) < 0.07
```

In `tests/test_config.py`, update the expected dict in `test_load_config_applies_defaults_when_omitted` to add `"extraction_cascade": False,`, and append:

```python
def test_load_config_reads_extraction_cascade(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - a\nextraction_cascade: true\n")
    assert load_config(str(config_path))["extraction_cascade"] is True
```

Append to `tests/test_run_weekly.py`:

```python
def test_run_all_passes_the_cascade_flag_to_extract(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\nextraction_cascade: true\n")
    seen = {}
    for stage in ("fetch", "match", "report", "materiality"):
        monkeypatch.setattr(getattr(run_weekly, stage), "run", lambda *a, **kw: None)
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: seen.update(kw))

    run_weekly.run_all(config_path=str(config_path))

    assert seen["cascade"] is True
```

Append to `tests/test_query.py`:

```python
def test_triage_audit_reports_false_skips(tmp_path, capsys):
    conn = db.connect(seed_db(tmp_path))
    evidence_id = conn.execute("SELECT evidence_id FROM evidence").fetchone()[0]
    call_id = ledger.start_call(conn, stage="triage", subject_key=evidence_id, model="claude-haiku-5-5",
                                prompt_template="T", request_meta={"audit": True, "threshold": 0.9})
    ledger.finish_call(conn, call_id, status="succeeded", parsed={"route": "skip", "confidence": 0.95, "reason": "x"})
    db.record_extraction_outcome(conn, evidence_id, "candidate", None, decided_by="extractor")
    conn.commit()

    query.triage_audit_command(conn)

    output = json.loads(capsys.readouterr().out)
    assert (output["audited_skips"], output["false_skips"], output["false_skip_rate"]) == (1, 1, 1.0)
    assert output["false_skip_evidence_ids"] == [evidence_id]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_extract.py tests/test_config.py tests/test_run_weekly.py tests/test_query.py -v`
Expected: FAIL (`unexpected keyword argument 'cascade'`, missing config key, missing `triage_audit_command`).

- [ ] **Step 3: Add the cascade to `extract.py`**

Add `import hashlib` and `import triage` to the imports. Add after `MAX_CONSECUTIVE_API_ERRORS`:

```python
TRIAGE_AUDIT_RATE = 0.05


def in_audit_sample(evidence_id, rate=TRIAGE_AUDIT_RATE):
    """Deterministic: a given evidence row is always in, or always out of, the audit sample."""
    return int(hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF < rate


def _triage_one(conn, client, evidence):
    """Run triage through the ledger. Returns True only when triage skipped the post (outcome
    recorded and committed). Any failure returns False, so the extractor sees the post (fail open)."""
    evidence_id = evidence["evidence_id"]
    audit = in_audit_sample(evidence_id)
    call_id = ledger.start_call(
        conn, stage="triage", subject_key=evidence_id, model=triage.TRIAGE_MODEL,
        prompt_template=triage.TRIAGE_PROMPT_TEMPLATE,
        request_meta={"audit": audit, "threshold": triage.TRIAGE_SKIP_THRESHOLD},
    )
    try:
        reply = triage.request_triage(client, evidence)
    except triage.TriageAPIError as e:
        ledger.finish_call(conn, call_id, status="api_error", error=str(e))
        conn.commit()
        return False
    try:
        decision = triage.parse_triage_reply(reply, evidence_id)
    except triage.TriageResponseError as e:
        ledger.finish_call(conn, call_id, status="response_error", reply=reply, error=str(e))
        conn.commit()
        return False

    ledger.finish_call(conn, call_id, status="succeeded", reply=reply,
                       parsed={"route": decision.route, "confidence": decision.confidence, "reason": decision.reason})
    skipped = triage.should_skip(decision) and not audit
    if skipped:
        db.record_extraction_outcome(conn, evidence_id, "skipped", call_id, decided_by="triage")
    conn.commit()
    return skipped
```

Change `extract_one` to take the flag, and short-circuit at the top:

```python
def extract_one(conn, client, evidence, cascade=False):
    """... (docstring unchanged). With cascade=True a triage call runs first and may skip the post."""
    if cascade and _triage_one(conn, client, evidence):
        return "triaged_skip"
    evidence_id = evidence["evidence_id"]
    # ... rest unchanged
```

Change `run` to take and pass the flag, and to abandon stale triage calls too:

```python
def run(db_path="data/pi_agent.db", today=None, client=None, cascade=False):
    ...
        for stage in ("triage", "extract"):
            abandoned = ledger.abandon_stale_calls(conn, stage)
            if abandoned:
                print(f"marked {abandoned} interrupted {stage} call(s) abandoned; their evidence will be retried")
        ...
            outcome = extract_one(conn, client, evidence, cascade=cascade)
```

- [ ] **Step 4: Config and orchestrator**

`config.py`, in the returned dict:

```python
        "extraction_cascade": bool(raw.get("extraction_cascade", False)),
```

`config.yaml.example`, append:

```yaml
# Cheap Haiku triage before extraction; it can only skip obvious noise. Off until you have read
# the triage eval (python evals/run_triage_eval.py) and ADR 007.
extraction_cascade: false
```

`run_weekly.py`: in `run_all`, `extract.run(today=today, cascade=config["extraction_cascade"])`. In `main`, `extract.run(cascade=config["extraction_cascade"])`.

- [ ] **Step 5: Audit read and command**

`db.py`:

```python
def triage_audit(conn):
    """Audit-sample triage calls that would have skipped (route=skip at or above their threshold),
    against what the extractor then decided. A 'candidate' outcome is a false skip."""
    rows = conn.execute(
        "SELECT c.subject_key AS evidence_id, x.status AS outcome FROM llm_call c "
        "LEFT JOIN evidence_extraction x ON x.evidence_id = c.subject_key "
        "WHERE c.stage = 'triage' AND c.status = 'succeeded' "
        "AND json_extract(c.request_meta, '$.audit') = 1 "
        "AND json_extract(c.parsed, '$.route') = 'skip' "
        "AND json_extract(c.parsed, '$.confidence') >= json_extract(c.request_meta, '$.threshold') "
        "ORDER BY c.call_id"
    ).fetchall()
    false_ids = [r["evidence_id"] for r in rows if r["outcome"] == "candidate"]
    agreed = sum(r["outcome"] == "skipped" for r in rows)
    decided = agreed + len(false_ids)
    return {
        "audited_skips": len(rows),
        "extractor_agreed": agreed,
        "false_skips": len(false_ids),
        "pending": len(rows) - decided,
        "false_skip_rate": len(false_ids) / decided if decided else None,
        "false_skip_evidence_ids": false_ids,
    }
```

`query.py`: add

```python
def triage_audit_command(conn):
    print(json.dumps(db.triage_audit(conn), indent=2))
```

register `subparsers.add_parser("triage-audit", help="how often triage skips were wrong, from the 5% audit sample")`, and dispatch `elif args.command == "triage-audit": triage_audit_command(conn)`.

- [ ] **Step 6: Run the tests**

Run: `pytest tests/test_extract.py tests/test_config.py tests/test_run_weekly.py tests/test_query.py tests/test_resume.py -v`
Expected: PASS.

- [ ] **Step 7: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add extract.py config.py config.yaml.example run_weekly.py db.py query.py tests/test_extract.py tests/test_config.py tests/test_run_weekly.py tests/test_query.py
git commit -m "feat(extract): optional triage cascade with fail-open behavior and a 5% production audit"
```

---

### Task 5: Decide, document, and (only if justified) turn it on

**Files:**
- Create: `docs/decisions/007-triage-before-extraction.md`
- Modify: `README.md`

- [ ] **Step 1: Run the measurements (manual, costs API calls)**

```powershell
python evals/run_triage_eval.py
python evals/run_extraction_eval.py                 # if Task 1's baseline is stale
python query.py usage --since 2026-09-01            # real weekly extraction volume and tokens
```

- [ ] **Step 2: Write ADR 007 with the numbers filled in**

```markdown
# Decision

Optional Claude Haiku 5.5 triage before extraction (`extraction_cascade`). Triage may only skip, trusts a skip only at confidence >= TRIAGE_SKIP_THRESHOLD, and fails open. A deterministic 5% of trusted skips is still extracted, as a production audit.

## Context

Every post cost one Sonnet 5 call with adaptive thinking, obvious spam included. Haiku 5.5 is about 20x cheaper per token ($0.10 / $0.50 vs $2 / $10 per MTok). The risk is a false skip: a real signal the extractor never sees, which no later stage can recover.

## Options considered

- **Sonnet at lower effort** (measured first, per the Claude API guidance on cascades): one model and one prompt, no new failure mode.
- **Haiku triage** (this ADR): a second narrow call, consistent with ADR 001. It only removes work, never adds a classification.
- **Batch API** (50% off): orthogonal, but needs asynchronous collection. Deferred.

## Measurements (fill in from the PR runs)

| Option | skip acc. | signal_type acc. | false skips @ threshold | tokens / post (in, out) | est. $ / 1000 posts |
| --- | --- | --- | --- | --- | --- |
| Sonnet, default effort | | | n/a | | |
| Sonnet, effort low | | | n/a | | |
| Sonnet, effort medium | | | n/a | | |
| Haiku triage @ 0.90 + Sonnet default | | | | | |

Decision rule:
- Enable the cascade only if the chosen threshold has **zero false skips** on the golden set, including `prompt_injection.yaml` once P5 adds it.
- And it must save more than the best Sonnet-effort option at equal `skip` and `signal_type` accuracy.

Outcome: <enabled at threshold X / not enabled, because ...>

## Consequences

- With the cascade on, `python query.py triage-audit` reports the false-skip rate on the audit sample. Check it after four weekly runs; any false skip means raise the threshold or turn the cascade off.
- Changing `TRIAGE_SKIP_THRESHOLD` or the prompt is a reviewed code change (CLAUDE.md: prompt contracts are human-designed).
```

- [ ] **Step 3: Update `README.md`**

1. **AI design decisions table:** add `| Cost | Optional Haiku triage that can only skip, behind extraction_cascade | ~20x cheaper per token on noise; fails open; false-skip rate measured on golden data and a 5% production audit (ADR 007) |`.
2. **Evals:** add a paragraph:

   > `python evals/run_triage_eval.py` grades triage against the extraction golden set's skip labels and prints a skip-threshold sweep with every false skip. `python evals/run_extraction_eval.py --effort low|medium` measures the cheaper-effort alternative; the effort is recorded and comparisons warn when it changes.
3. **Setup:** after step 5, add "Optional: set `extraction_cascade: true` in `config.yaml` after reading ADR 007."

- [ ] **Step 4: Run everything and commit**

Run: `pytest -q && ruff check .`

```bash
git add docs/decisions/007-triage-before-extraction.md README.md
git commit -m "docs: ADR 007 triage before extraction, with measurements and the enable rule"
```

- [ ] **Step 5: Enable only if the decision rule holds (owner action)**

If ADR 007's outcome is "enabled", set `extraction_cascade: true` in the local, gitignored `config.yaml`. Put a reminder in your own calendar or tracker to run `python query.py triage-audit` after four weekly runs.

---

## Self-review notes

- **Spec coverage:**
  - C1: Tasks 2 and 4.
  - Cascade guardrail (§4.8, "measure lower effort first"): Task 1, Step 6, and the ADR 007 table.
  - Spec P4 decisions:
    - 1 (effort first): Task 1;
    - 2 (skip-only, threshold, fail open): Tasks 2 and 4;
    - 3 (deterministic audit and `triage-audit`): Task 4;
    - 4 (off by default, owner decides): Tasks 4 and 5.
- **Type consistency:**
  - `TriageDecision(route, confidence, reason)` is used the same way in `triage.py`, `triage_eval.py` and `extract._triage_one`.
  - The ledger's `parsed` keys (`route`, `confidence`) match the JSON paths in `db.triage_audit`.
- **Migration:** none. Spec §3 records that P5 uses number 8.
