# Eval Run History & Judge Rubric Tuning Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two deferred open items from the evals design. Save every eval run so pass rates can be compared across runs, and give the summary judge a human-labeled calibration set so its rubric can be tuned against evidence, not by feel.

**Architecture:** A new `evals/runs.py` turns a graded run into a JSON record under a gitignored `evals/results/`. Each record carries the models used, a fingerprint of every prompt, and a fingerprint of the golden data. `runs.compare_runs` diffs two records into pass-rate and per-field deltas plus per-example flips, and names any model, prompt or golden-data change that makes the two runs not directly comparable. The judge gets its own golden kind (`evals/golden/judge/*.yaml`: evidence, reference summary, a candidate summary, and a human `pass`/`fail`). That kind has a runner that measures judge-vs-human agreement and an export script that drafts cases from a real extraction run. The rubric tuning itself (Task 6) is a measured, manual procedure run once real output exists, not code written up front.

**Tech Stack:** Python 3.11+ (CI runs 3.11), Anthropic Python SDK, PyYAML, pytest, ruff.

**Spec:** [docs/superpowers/specs/2026-09-24-extraction-matching-evals-design.md](../specs/2026-09-24-extraction-matching-evals-design.md), section "Open items for implementation planning" (items 1 and 2). The spec left both open, so this plan makes the design decisions below. Read the spec for the harness this builds on.

## Design decisions (made here, since the spec left these open)

1. **Storage:** one JSON file per run, `evals/results/<kind>-<UTC YYYYMMDDTHHMMSSZ>[-N].json`, gitignored (as the spec suggested). Runners save by default; `--no-save` opts out. A save failure warns and never changes the exit code or loses the printed report.
2. **Comparability over trends:** each record stores `models`, `prompts` (a 12-hex SHA-256 fingerprint of each prompt template; no manual version numbers to forget to bump), `golden_fingerprint` (over the graded examples after `--filter`), `filter` and `git_commit`. The comparison warns when any of these differ, so "the pass rate dropped" can be told apart from "someone relabeled the golden set or edited the judge prompt".
3. **Drift view = pairwise diff:** `evals/compare_runs.py` compares latest vs previous, a pinned baseline vs latest, or two named files. A multi-run trend table is out of scope (YAGNI). The JSON files keep that possible later.
4. **Judge tuning is measured against humans:** the judge is only trustworthy if it agrees with a human on whether a summary is acceptable. Tuning without a labeled set just moves the pass rate around. So the order is: calibration set, then a baseline agreement number, then rubric edits, then accept only if agreement improves on a held-out split. The split is `evals/golden/judge/tuning.yaml` (look at freely) vs `evals/golden/judge/holdout.yaml` (only scored, never read while editing).
5. **Record format is not a public contract:** `schema_version: 1`. `load_run` refuses other versions with a clear error rather than half-reading them.

## Global Constraints

- Evals are **not a CI gate**. Nothing in `evals/` may be named `test_*.py`, and no pytest test may make a real API call. The tests added here inject fake clients.
- Runners call the real production functions (`extract.extract_topic`, `match.call_matcher`, `judge.judge_summary`). No copies of prompt text in `evals/`. Fingerprints are computed from the live constants `extract.EXTRACTION_PROMPT_TEMPLATE`, `match.MATCHING_PROMPT_TEMPLATE` and `judge.JUDGE_PROMPT_TEMPLATE`.
- The real client is obtained exactly like production: `anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))`. Runners gain a `client=None` parameter on `main()` for tests only. When it is `None`, behavior is unchanged.
- Exit codes: 0 whenever a report is printed (grading outcomes and save failures never change it). 2 for malformed golden YAML, a `--filter` that matches nothing, missing credentials, or (for `compare_runs.py` / `export_judge_cases.py`) unusable input files.
- `evals/results/` is gitignored and holds only machine-written files.
- Run files are written as UTF-8 with `ensure_ascii=False`. Every file read and write passes `encoding="utf-8"` (the Windows default is cp1252).
- Console scripts call `sys.stdout.reconfigure(errors="backslashreplace")` before printing model text.
- Judge model stays `claude-opus-5` unless the user decides otherwise. Tuning changes the rubric wording, not the model.
- Lint: `ruff check .` must pass (rules E, F, W; E501 ignored). Runner scripts need `# noqa: E402` on imports that follow the `sys.path` insertion.
- No Python 3.12+ syntax (CI is 3.11).
- Commits end with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Junk in `evals/results/`.** Expected: a half-written file, a hand-edited file or a future `schema_version` is skipped with a warning by `compare_runs.py --kind`. It does not crash the comparison. Naming such a file explicitly exits 2 with the path in the message. Pinned in Task 1 (`find_runs` / `load_run`) and Task 3 (CLI).
2. **Comparing runs that aren't comparable.** Expected: when golden data, `--filter`, a prompt or a model differ between the two runs, the deltas are still shown, but a warnings block above them names exactly what changed. Comparing an extraction run with a matching run is refused. Pinned in Task 3.
3. **Two runs in the same second** (e.g. a filtered re-run right after a full one). Expected: the second gets a `-2` suffix, never overwrites the first, and sorts after it. Pinned in Task 1.
4. **Saving fails after a paid run** (unwritable or bad `--results-dir`). Expected: the report is already on screen, a warning goes to stderr, and the exit code is still 0. Pinned in Task 2.
5. **An unlabeled calibration case gets committed** (`human_verdict: null` straight out of the export). Expected: the loader fails fast naming the file and case id and saying the case is unlabeled, and the CI golden-data test catches it. Pinned in Task 4.

---

## File Structure

| File | Responsibility |
|---|---|
| `evals/runs.py` (create) | Fingerprints, run-record build/save/load/find, run comparison and its console format, runner save helper |
| `evals/run_extraction_eval.py`, `evals/run_matching_eval.py` (modify) | Save each run; `--no-save`, `--results-dir`; `client` injection for tests |
| `evals/compare_runs.py` (create) | CLI: pick two run files and print the comparison |
| `evals/harness.py` (modify) | `JudgeCase` golden kind (loader, filter), `grade_judge_case`, `judge_agreement`, `format_judge_agreement` |
| `evals/golden/judge/seed.yaml` (create) | Synthetic calibration cases so the kind is exercised in CI before real cases exist |
| `evals/run_judge_eval.py` (create) | CLI: grade the judge against human labels, save the run |
| `evals/export_judge_cases.py` (create) | CLI: draft unlabeled calibration cases from a saved extraction run |
| `evals/judge.py` (modify, Task 6 only) | Rubric wording, after measurement |
| `.gitignore`, `README.md`, the spec (modify) | Ignore results; document run history, comparison, judge calibration and tuning |
| `tests/test_evals_runs.py`, `tests/test_evals_runners.py` (create); `tests/test_evals_harness.py`, `tests/test_evals_golden.py` (modify) | Unit tests (fakes only) |

**Branching:** from an up-to-date `main`: `git checkout main && git pull && git checkout -b feat/evals-run-history-and-judge-calibration`.

**Prerequisite for Task 6 only (not for Tasks 1-5):** the draft Reddit golden labels (29 `REVIEW` flags in `evals/golden/extraction/database_development_reddit.yaml`, plus flags in 3 matching scenarios) should be reviewed first. Calibration cases inherit their `reference_summary` from those examples.

---

### Task 1: Run records: fingerprint, build, save, load, find

**Files:**
- Create: `evals/runs.py`
- Modify: `.gitignore`
- Test: `tests/test_evals_runs.py` (create)

**Interfaces:**
- Consumes: `harness.ExampleResult`, `harness.FieldResult`, `harness.Report`, `harness.aggregate(results) -> Report` (existing).
- Produces:
  - `runs.SCHEMA_VERSION = 1`, `runs.KINDS = ("extraction", "matching", "judge")`, `runs.DEFAULT_RESULTS_DIR: Path` (= `evals/results`)
  - `runs.RunFileError(Exception)`
  - `runs.fingerprint(value) -> str` (12 hex chars; accepts a str or any JSON-serializable value)
  - `runs.golden_fingerprint(items: list[dataclass]) -> str`
  - `runs.git_commit() -> str | None`
  - `runs.build_run_record(kind, results, report, *, models: dict, prompts: dict, golden: str, filter_name: str | None, started_at: datetime, commit: str | None) -> dict`
  - `runs.save_run(record, results_dir=DEFAULT_RESULTS_DIR) -> Path`
  - `runs.load_run(path) -> dict`
  - `runs.find_runs(results_dir, kind) -> tuple[list[tuple[Path, dict]], list[Path]]`: (runs oldest first, unreadable paths)
  - Record shape: `{"schema_version", "kind", "started_at" (UTC ISO, seconds), "git_commit", "filter", "models": {name: model}, "prompts": {name: fingerprint}, "golden_fingerprint", "summary": {"total", "passed", "failed", "errored", "field_accuracy": {field: [correct, graded]}, "judge_passed", "judge_total"}, "results": [dataclasses.asdict(ExampleResult)]}`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_evals_runs.py`:

```python
import dataclasses
import datetime
import json

import pytest

from evals import harness, runs

STARTED = datetime.datetime(2026, 9, 29, 10, 15, 0, tzinfo=datetime.timezone.utc)


def fr(field, passed, expected="x", actual="x", detail=None):
    return harness.FieldResult(field, field, expected, actual, passed, detail)


RESULTS = [
    harness.ExampleResult("db-001", "database_development", "passed", [
        fr("skip", True, False, False),
        fr("entity", True, {"company": "Oracle"}, {"type": "vendor", "company": "oracle", "product": None}),
        fr("summary", True, "ref", "Utilisateur mécontent 🙁", detail="Faithful."),
    ]),
    harness.ExampleResult("db-002", "database_development", "errored", error="API call failed: 529"),
]


def make_record(results=RESULTS, kind="extraction", **overrides):
    kwargs = dict(
        models={"extraction": "claude-sonnet-5", "judge": "claude-opus-5"},
        prompts={"extraction": "aaa111", "judge": "bbb222"},
        golden="ccc333", filter_name=None, started_at=STARTED, commit="39f4032",
    )
    kwargs.update(overrides)
    return runs.build_run_record(kind, results, harness.aggregate(results), **kwargs)


# --- fingerprints ---

def test_fingerprint_is_short_stable_and_content_sensitive():
    assert len(runs.fingerprint("abc")) == 12
    assert runs.fingerprint("abc") == runs.fingerprint("abc")
    assert runs.fingerprint("abc") != runs.fingerprint("abd")
    assert runs.fingerprint({"b": 1, "a": 2}) == runs.fingerprint({"a": 2, "b": 1})


def test_golden_fingerprint_changes_when_a_label_changes():
    example = harness.ExtractionExample(
        "db-001", "database_development", {"title": "t", "content": "c"},
        {"skip": False, "signal_type": "reliability_issue", "entity": None, "effective_date": None}, "ref",
    )
    relabeled = dataclasses.replace(example, expected={**example.expected, "signal_type": "usability_issue"})

    assert runs.golden_fingerprint([example]) == runs.golden_fingerprint([dataclasses.replace(example)])
    assert runs.golden_fingerprint([example]) != runs.golden_fingerprint([relabeled])


# --- building records ---

def test_build_run_record_captures_metadata_summary_and_results():
    record = make_record()

    assert record["schema_version"] == runs.SCHEMA_VERSION
    assert record["kind"] == "extraction"
    assert record["started_at"] == "2026-09-29T10:15:00+00:00"
    assert record["git_commit"] == "39f4032"
    assert record["filter"] is None
    assert record["models"] == {"extraction": "claude-sonnet-5", "judge": "claude-opus-5"}
    assert record["prompts"] == {"extraction": "aaa111", "judge": "bbb222"}
    assert record["golden_fingerprint"] == "ccc333"
    assert record["summary"] == {
        "total": 2, "passed": 1, "failed": 0, "errored": 1,
        "field_accuracy": {"skip": [1, 1], "entity": [1, 1], "summary": [1, 1]},
        "judge_passed": 1, "judge_total": 1,
    }
    first = record["results"][0]
    assert (first["example_id"], first["source"], first["status"]) == ("db-001", "database_development", "passed")
    assert first["fields"][2] == {
        "field": "summary", "label": "summary", "expected": "ref", "actual": "Utilisateur mécontent 🙁",
        "passed": True, "detail": "Faithful.",
    }
    assert record["results"][1]["error"] == "API call failed: 529"


def test_build_run_record_stores_started_at_in_utc():
    local = STARTED.astimezone(datetime.timezone(datetime.timedelta(hours=3)))

    assert make_record(started_at=local)["started_at"] == "2026-09-29T10:15:00+00:00"


def test_build_run_record_rejects_unknown_kind():
    with pytest.raises(ValueError, match="summaries"):
        make_record(kind="summaries")


# --- saving and loading ---

def test_save_and_load_round_trip_keeps_non_ascii_text(tmp_path):
    path = runs.save_run(make_record(), tmp_path)

    assert path.name == "extraction-20260929T101500Z.json"
    assert runs.load_run(path) == make_record()
    assert "Utilisateur mécontent 🙁" in path.read_text(encoding="utf-8")


def test_save_run_never_overwrites_a_run_from_the_same_second(tmp_path):
    first = runs.save_run(make_record(), tmp_path)
    second = runs.save_run(make_record(filter_name="database_development"), tmp_path)

    assert second.name == "extraction-20260929T101500Z-2.json"
    assert runs.load_run(first)["filter"] is None
    assert runs.load_run(second)["filter"] == "database_development"


def test_save_run_creates_missing_results_dir(tmp_path):
    assert runs.save_run(make_record(), tmp_path / "nested" / "results").exists()


@pytest.mark.parametrize("content", [
    "{not json",
    json.dumps([1, 2]),
    json.dumps({"schema_version": 99, "kind": "extraction", "summary": {}, "results": []}),
    json.dumps({"schema_version": 1, "kind": "bogus", "summary": {}, "results": []}),
    json.dumps({"schema_version": 1, "kind": "extraction", "summary": {}}),
])
def test_load_run_rejects_unreadable_or_foreign_files_naming_the_path(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(runs.RunFileError, match="bad.json"):
        runs.load_run(path)


def test_load_run_missing_file_names_the_path(tmp_path):
    with pytest.raises(runs.RunFileError, match="missing.json"):
        runs.load_run(tmp_path / "missing.json")


def test_find_runs_orders_oldest_first_filters_kind_and_skips_bad_files(tmp_path):
    newest = runs.save_run(make_record(started_at=STARTED + datetime.timedelta(hours=1)), tmp_path)
    oldest = runs.save_run(make_record(), tmp_path)
    same_second = runs.save_run(make_record(), tmp_path)  # saved as ...Z-2.json
    runs.save_run(make_record(kind="matching"), tmp_path)
    (tmp_path / "extraction-garbage.json").write_text("{", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not a run", encoding="utf-8")

    found, skipped = runs.find_runs(tmp_path, "extraction")

    assert [path for path, _ in found] == [oldest, same_second, newest]
    assert [path.name for path in skipped] == ["extraction-garbage.json"]


def test_find_runs_on_missing_dir_finds_nothing(tmp_path):
    assert runs.find_runs(tmp_path / "never-created", "extraction") == ([], [])
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_runs.py -q`
Expected: collection error, `ImportError: cannot import name 'runs' from 'evals'`.

- [ ] **Step 3: Implement `evals/runs.py` (records part)**

```python
"""Save eval runs as JSON files and compare two runs to spot drift.

Run files live in evals/results/ (gitignored), one per run. Each records the models, a
fingerprint of every prompt, and a fingerprint of the golden data it graded, so a change
in scores can be traced to the model, a prompt edit, or a golden-set edit.
"""
import dataclasses
import datetime
import hashlib
import itertools
import json
import re
import subprocess
from pathlib import Path

SCHEMA_VERSION = 1
KINDS = ("extraction", "matching", "judge")
DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"

_SAVE_SUFFIX_RE = re.compile(r"Z-(\d+)$")


class RunFileError(Exception):
    """A run file is missing, unreadable, or not a run record this code understands."""


def fingerprint(value):
    """First 12 hex chars of a SHA-256 over a string, or over any JSON-serializable value."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def golden_fingerprint(items):
    """Fingerprint of the golden examples, scenarios, or judge cases a run graded (after --filter)."""
    return fingerprint([dataclasses.asdict(item) for item in items])


def git_commit():
    """Short HEAD commit, or None when git is unavailable or this is not a checkout."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def build_run_record(kind, results, report, *, models, prompts, golden, filter_name, started_at, commit):
    if kind not in KINDS:
        raise ValueError(f"unknown run kind {kind!r}")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "started_at": started_at.astimezone(datetime.timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "filter": filter_name,
        "models": dict(models),
        "prompts": dict(prompts),
        "golden_fingerprint": golden,
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "errored": report.errored,
            "field_accuracy": {name: [correct, graded] for name, (correct, graded) in report.field_accuracy.items()},
            "judge_passed": report.judge_passed,
            "judge_total": report.judge_total,
        },
        "results": [dataclasses.asdict(result) for result in results],
    }


def save_run(record, results_dir=DEFAULT_RESULTS_DIR):
    """Write `record` to <results_dir>/<kind>-<UTC stamp>.json, never overwriting an earlier run."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.fromisoformat(record["started_at"]).strftime("%Y%m%dT%H%M%SZ")
    text = json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n"
    for attempt in itertools.count(1):
        path = results_dir / (f"{record['kind']}-{stamp}" + ("" if attempt == 1 else f"-{attempt}") + ".json")
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(text)
        except FileExistsError:
            continue
        return path


def load_run(path):
    path = Path(path)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RunFileError(f"{path}: no such run file") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RunFileError(f"{path}: not a readable JSON file ({e})") from e
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        raise RunFileError(f"{path}: not a schema v{SCHEMA_VERSION} eval run file")
    if (record.get("kind") not in KINDS or not isinstance(record.get("summary"), dict)
            or not isinstance(record.get("results"), list)):
        raise RunFileError(f"{path}: run file is missing its kind, summary, or results")
    return record


def find_runs(results_dir, kind):
    """Saved runs of `kind` as (path, record) pairs, oldest first, plus the paths that could not be loaded."""
    found, skipped = [], []
    for path in Path(results_dir).glob(f"{kind}-*.json"):
        try:
            record = load_run(path)
        except RunFileError:
            skipped.append(path)
            continue
        if record["kind"] == kind:
            found.append((path, record))
    found.sort(key=lambda pair: (str(pair[1].get("started_at")), _save_order(pair[0])))
    return found, sorted(skipped)


def _save_order(path):
    # Runs saved within the same second are named ...Z.json, ...Z-2.json, ...Z-3.json.
    suffix = _SAVE_SUFFIX_RE.search(path.stem)
    return int(suffix.group(1)) if suffix else 1
```

- [ ] **Step 4: Ignore the results directory**

Append to `.gitignore`:

```text
evals/results/
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_evals_runs.py -q`
Expected: all pass.

- [ ] **Step 6: Full suite + lint**

Run: `python -m pytest -q; ruff check .`
Expected: all pass, `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add evals/runs.py tests/test_evals_runs.py .gitignore
git commit -m "feat(evals): save eval runs as JSON records with prompt and golden fingerprints

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Runners save every run

**Files:**
- Modify: `evals/runs.py` (add `save_and_announce`)
- Modify: `evals/run_extraction_eval.py`, `evals/run_matching_eval.py`
- Test: `tests/test_evals_runners.py` (create)

**Interfaces:**
- Consumes: everything `runs` produced in Task 1; `harness.load_golden_dir`, `harness.filter_examples`, `harness.aggregate`, `harness.format_report`, `harness.grade_extraction_example`, `harness.grade_matching_scenario` (existing).
- Produces:
  - `runs.save_and_announce(record, results_dir) -> Path | None`: prints `saved run to <path>` on stdout, or `warning: could not save run results: <error>` on stderr and returns `None`.
  - `run_extraction_eval.main(argv=None, client=None) -> int` and `run_matching_eval.main(argv=None, client=None) -> int`, with new flags `--no-save` and `--results-dir DIR` (default `runs.DEFAULT_RESULTS_DIR`).
  - Record `models`/`prompts` keys: extraction runs use `{"extraction", "judge"}`; matching runs use `{"matching"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_evals_runners.py`:

```python
import json

import extract
import match
from evals import harness, judge, run_extraction_eval, run_matching_eval, runs


class FakeContentBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeResponse:
    def __init__(self, text):
        self.content = [FakeContentBlock(text)]
        self.stop_reason = "end_turn"


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


class RepeatingMessages:
    def __init__(self, result):
        self._result = result

    def create(self, **kwargs):
        if isinstance(self._result, Exception):
            raise self._result
        return FakeResponse(self._result)


class RepeatingClient:
    """Answers every call with the same text, or raises the same exception, however many calls a run makes."""

    def __init__(self, result):
        self.messages = RepeatingMessages(result)


MATCH_OK = json.dumps([{"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None}])
POOLING = ["--filter", "connection_pooling_duplicate"]


def test_matching_runner_saves_a_run_record(tmp_path, capsys):
    code = run_matching_eval.main([*POOLING, "--results-dir", str(tmp_path)], client=FakeClient([MATCH_OK]))

    assert code == 0
    found, skipped = runs.find_runs(tmp_path, "matching")
    [(path, record)] = found
    assert skipped == []
    assert record["filter"] == "connection_pooling_duplicate"
    assert record["models"] == {"matching": match.MATCH_MODEL}
    assert record["prompts"] == {"matching": runs.fingerprint(match.MATCHING_PROMPT_TEMPLATE)}
    assert record["summary"]["passed"] == 1
    assert [r["example_id"] for r in record["results"]] == ["connection_pooling_duplicate"]
    assert f"saved run to {path}" in capsys.readouterr().out


def test_extraction_runner_saves_errored_run_with_both_prompts(tmp_path):
    client = RepeatingClient(RuntimeError("529 overloaded"))

    code = run_extraction_eval.main(["--filter", "database_development", "--results-dir", str(tmp_path)], client=client)

    assert code == 0
    [(_, record)], _ = runs.find_runs(tmp_path, "extraction")
    assert record["summary"]["total"] > 0
    assert record["summary"]["errored"] == record["summary"]["total"]
    assert record["prompts"] == {
        "extraction": runs.fingerprint(extract.EXTRACTION_PROMPT_TEMPLATE),
        "judge": runs.fingerprint(judge.JUDGE_PROMPT_TEMPLATE),
    }
    assert set(record["models"]) == {"extraction", "judge"}


def test_no_save_writes_nothing(tmp_path):
    code = run_matching_eval.main([*POOLING, "--no-save", "--results-dir", str(tmp_path)], client=FakeClient([MATCH_OK]))

    assert code == 0
    assert list(tmp_path.iterdir()) == []


def test_failed_save_warns_but_keeps_report_and_exit_code(tmp_path, capsys):
    not_a_dir = tmp_path / "file-in-the-way"
    not_a_dir.write_text("x", encoding="utf-8")

    code = run_matching_eval.main([*POOLING, "--results-dir", str(not_a_dir)], client=FakeClient([MATCH_OK]))

    captured = capsys.readouterr()
    assert code == 0
    assert "== Eval report ==" in captured.out
    assert "warning: could not save run results" in captured.err


def test_unknown_filter_exits_2_without_saving(tmp_path):
    code = run_matching_eval.main(["--filter", "no_such_scenario", "--results-dir", str(tmp_path)],
                                  client=FakeClient([]))

    assert code == 2
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_runners.py -q`
Expected: FAIL: `main() got an unexpected keyword argument 'client'`.

- [ ] **Step 3: Add the save helper to `evals/runs.py`**

Add `import sys` to the imports, then append:

```python
def save_and_announce(record, results_dir):
    """Runner helper: save the run and say where. A failed save warns but never fails the run,
    because the report has already been printed and the API calls already paid for."""
    try:
        path = save_run(record, results_dir)
    except OSError as e:
        print(f"warning: could not save run results: {e}", file=sys.stderr)
        return None
    print(f"\nsaved run to {path}")
    return path
```

- [ ] **Step 4: Update `evals/run_extraction_eval.py`**

Replace the whole file with:

```python
"""Run the extraction golden set against the real extraction prompt and model.

Usage: python evals/run_extraction_eval.py [--filter database_development] [--model MODEL] [--no-save]

Calls the real Anthropic API (one extraction call plus one judge call per non-skipped
example). Each run is saved as JSON under evals/results/; compare runs with
evals/compare_runs.py. This is a manual tool, not part of pytest or CI.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import extract  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, judge, runs  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "extraction")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Grade extract.extract_topic against the golden set.")
    parser.add_argument("--filter", help="only run the golden file with this basename, e.g. database_development")
    parser.add_argument("--model", help=f"override extract.EXTRACTION_MODEL (default: {extract.EXTRACTION_MODEL})")
    parser.add_argument("--no-save", action="store_true", help="do not save this run to --results-dir")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR),
                        help="where to save the run (default: evals/results)")
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

    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    print(f"extraction model: {extract.EXTRACTION_MODEL}, judge model: {judge.JUDGE_MODEL}")
    results = []
    for number, example in enumerate(selected, 1):
        result = harness.grade_extraction_example(example, client)
        print(f"[{number}/{len(selected)}] {example.source}/{example.id}: {result.status}")
        results.append(result)

    report = harness.aggregate(results)
    print()
    print(harness.format_report(report))

    if not args.no_save:
        record = runs.build_run_record(
            "extraction", results, report,
            models={"extraction": extract.EXTRACTION_MODEL, "judge": judge.JUDGE_MODEL},
            prompts={
                "extraction": runs.fingerprint(extract.EXTRACTION_PROMPT_TEMPLATE),
                "judge": runs.fingerprint(judge.JUDGE_PROMPT_TEMPLATE),
            },
            golden=runs.golden_fingerprint(selected),
            filter_name=args.filter, started_at=started_at, commit=runs.git_commit(),
        )
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Update `evals/run_matching_eval.py`**

Replace the whole file with:

```python
"""Run the matching golden set against the real matching prompt and model.

Usage: python evals/run_matching_eval.py [--filter connection_pooling_duplicate] [--model MODEL] [--no-save]

Calls the real Anthropic API (one matcher call per scenario). Each run is saved as JSON
under evals/results/; compare runs with evals/compare_runs.py. This is a manual tool, not
part of pytest or CI.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import match  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, runs  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "matching")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Grade match.call_matcher against the golden scenarios.")
    parser.add_argument("--filter", help="only run the scenario with this name, e.g. connection_pooling_duplicate")
    parser.add_argument("--model", help=f"override match.MATCH_MODEL (default: {match.MATCH_MODEL})")
    parser.add_argument("--no-save", action="store_true", help="do not save this run to --results-dir")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR),
                        help="where to save the run (default: evals/results)")
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

    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    print(f"matching model: {match.MATCH_MODEL}")
    results = []
    for number, scenario in enumerate(selected, 1):
        result = harness.grade_matching_scenario(scenario, client)
        print(f"[{number}/{len(selected)}] {scenario.name}: {result.status}")
        results.append(result)

    report = harness.aggregate(results)
    print()
    print(harness.format_report(report))

    if not args.no_save:
        record = runs.build_run_record(
            "matching", results, report,
            models={"matching": match.MATCH_MODEL},
            prompts={"matching": runs.fingerprint(match.MATCHING_PROMPT_TEMPLATE)},
            golden=runs.golden_fingerprint(selected),
            filter_name=args.filter, started_at=started_at, commit=runs.git_commit(),
        )
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 6: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_evals_runners.py -q`
Expected: all pass.

- [ ] **Step 7: Full suite + lint**

Run: `python -m pytest -q; ruff check .`
Expected: all pass, `All checks passed!`.

- [ ] **Step 8: Commit**

```bash
git add evals/runs.py evals/run_extraction_eval.py evals/run_matching_eval.py tests/test_evals_runners.py
git commit -m "feat(evals): save every extraction and matching run under evals/results

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Compare two runs

**Files:**
- Modify: `evals/runs.py` (add `Comparison`, `compare_runs`, `format_comparison`)
- Create: `evals/compare_runs.py`
- Test: `tests/test_evals_runs.py` (append), `tests/test_evals_runners.py` (append)

**Interfaces:**
- Consumes: `runs.load_run`, `runs.find_runs`, `runs.KINDS`, `runs.DEFAULT_RESULTS_DIR`, `runs.RunFileError` (Task 1).
- Produces:
  - `runs.Comparison` dataclass: `kind: str`, `baseline_label: str`, `current_label: str`, `warnings: list[str]`, `pass_rate: tuple[float | None, float | None]`, `field_accuracy: dict[str, tuple[list | None, list | None]]`, `regressions: list[tuple[str, str]]` (key, why it now fails), `fixes: list[str]`, `status_changes: list[tuple[str, str, str]]`, `only_in_baseline: list[str]`, `only_in_current: list[str]`. Keys are `"<source>/<example_id>"`.
  - `runs.compare_runs(baseline: dict, current: dict) -> Comparison`: raises `ValueError` for different kinds.
  - `runs.format_comparison(comparison) -> str` (ASCII layout).
  - `compare_runs.main(argv=None) -> int`: `[BASELINE [CURRENT]] [--kind KIND] [--results-dir DIR]`.

- [ ] **Step 1: Write the failing comparison tests**

Append to `tests/test_evals_runs.py`:

```python
# --- comparing runs ---

def result(example_id, status, fields=(), error=None):
    return harness.ExampleResult(example_id, "database_development", status, list(fields), error)


BASELINE_RESULTS = [
    result("db-001", "passed", [fr("signal_type", True)]),
    result("db-002", "passed", [fr("signal_type", True)]),
    result("db-003", "failed", [fr("signal_type", False, "reliability_issue", "usability_issue")]),
    result("db-004", "errored", error="529"),
    result("db-005", "passed", [fr("signal_type", True)]),
]
CURRENT_RESULTS = [
    result("db-001", "passed", [fr("signal_type", True)]),
    result("db-002", "failed", [fr("signal_type", False, "reliability_issue", "pricing_complaint")]),
    result("db-003", "passed", [fr("signal_type", True)]),
    result("db-004", "passed", [fr("signal_type", True)]),
    result("db-006", "passed", [fr("signal_type", True)]),
]


def test_compare_runs_classifies_flips_and_computes_rates():
    comparison = runs.compare_runs(make_record(BASELINE_RESULTS), make_record(CURRENT_RESULTS))

    assert comparison.kind == "extraction"
    assert comparison.warnings == []
    assert comparison.pass_rate == (0.75, 0.8)
    assert comparison.field_accuracy == {"signal_type": ([3, 4], [4, 5])}
    assert comparison.regressions == [
        ("database_development/db-002", "signal_type: expected 'reliability_issue', got 'pricing_complaint'"),
    ]
    assert comparison.fixes == ["database_development/db-003"]
    assert comparison.status_changes == [("database_development/db-004", "errored", "passed")]
    assert comparison.only_in_baseline == ["database_development/db-005"]
    assert comparison.only_in_current == ["database_development/db-006"]


def test_compare_runs_regression_detail_uses_error_when_there_are_no_fields():
    current = [result("db-001", "failed", error="non-JSON response"), *CURRENT_RESULTS[1:]]

    comparison = runs.compare_runs(make_record(BASELINE_RESULTS), make_record(current))

    assert ("database_development/db-001", "non-JSON response") in comparison.regressions


def test_compare_runs_warns_about_everything_that_makes_runs_incomparable():
    current = make_record(
        CURRENT_RESULTS,
        models={"extraction": "claude-sonnet-5-5", "judge": "claude-opus-5"},
        prompts={"extraction": "aaa111", "judge": "ddd444"},
        golden="eee555", filter_name="database_development",
    )

    warnings = runs.compare_runs(make_record(BASELINE_RESULTS), current).warnings

    assert warnings == [
        "extraction model changed: claude-sonnet-5 -> claude-sonnet-5-5",
        "judge prompt changed: bbb222 -> ddd444",
        "golden data changed (examples or labels were added, edited, or removed)",
        "--filter differs: None -> database_development",
    ]


def test_compare_runs_refuses_different_kinds():
    with pytest.raises(ValueError, match="extraction run with a matching run"):
        runs.compare_runs(make_record(), make_record(kind="matching"))


def test_format_comparison_shows_every_section():
    current = make_record(CURRENT_RESULTS, prompts={"extraction": "aaa111", "judge": "ddd444"})

    text = runs.format_comparison(runs.compare_runs(make_record(BASELINE_RESULTS), current))

    assert "== Run comparison (extraction) ==" in text
    assert "Baseline: 2026-09-29T10:15:00+00:00 @ 39f4032" in text
    assert "judge prompt changed: bbb222 -> ddd444" in text
    assert "Pass rate (errored excluded): 75.0% -> 80.0%  (+5.0 pts)" in text
    assert f"  {'signal_type':<16} 3/4 75.0% -> 4/5 80.0%  (+5.0 pts)" in text
    assert "Regressions (passed -> failed): 1" in text
    assert "  database_development/db-002: signal_type: expected 'reliability_issue', got 'pricing_complaint'" in text
    assert "Fixes (failed -> passed): 1" in text
    assert "  database_development/db-004: errored -> passed" in text
    assert "Only in baseline: database_development/db-005" in text
    assert "Only in current: database_development/db-006" in text
    text.encode("ascii")  # layout itself adds no non-ASCII characters


def test_format_comparison_handles_runs_with_nothing_graded():
    comparison = runs.compare_runs(make_record([]), make_record([result("db-001", "errored", error="529")]))

    text = runs.format_comparison(comparison)

    assert "Pass rate (errored excluded): n/a -> n/a" in text
    assert "Regressions (passed -> failed): 0" in text
    assert "Warnings" not in text
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_runs.py -q`
Expected: the new tests FAIL with `AttributeError: module 'evals.runs' has no attribute 'compare_runs'`.

- [ ] **Step 3: Implement the comparison in `evals/runs.py`**

Append:

```python
@dataclasses.dataclass
class Comparison:
    kind: str
    baseline_label: str
    current_label: str
    warnings: list
    pass_rate: tuple
    field_accuracy: dict
    regressions: list
    fixes: list
    status_changes: list
    only_in_baseline: list
    only_in_current: list


def compare_runs(baseline, current):
    """Diff two run records of the same kind. Warnings name every setting that differs between
    them, so a score change is not blamed on the model when the prompt or golden data moved."""
    if baseline["kind"] != current["kind"]:
        raise ValueError(f"cannot compare a {baseline['kind']} run with a {current['kind']} run")

    warnings = []
    for key, noun in (("models", "model"), ("prompts", "prompt")):
        before, after = baseline.get(key) or {}, current.get(key) or {}
        for name in sorted(before.keys() | after.keys()):
            if before.get(name) != after.get(name):
                warnings.append(f"{name} {noun} changed: {before.get(name)} -> {after.get(name)}")
    if baseline.get("golden_fingerprint") != current.get("golden_fingerprint"):
        warnings.append("golden data changed (examples or labels were added, edited, or removed)")
    if baseline.get("filter") != current.get("filter"):
        warnings.append(f"--filter differs: {baseline.get('filter')} -> {current.get('filter')}")

    before_status, after_status = _statuses(baseline), _statuses(current)
    regressions, fixes, status_changes = [], [], []
    for key in sorted(before_status.keys() & after_status.keys()):
        (was, _), (now, why) = before_status[key], after_status[key]
        if was == now:
            continue
        if (was, now) == ("passed", "failed"):
            regressions.append((key, why))
        elif (was, now) == ("failed", "passed"):
            fixes.append(key)
        else:
            status_changes.append((key, was, now))

    before_fields = baseline["summary"].get("field_accuracy") or {}
    after_fields = current["summary"].get("field_accuracy") or {}
    names = [*before_fields, *(name for name in after_fields if name not in before_fields)]

    return Comparison(
        kind=baseline["kind"],
        baseline_label=_label(baseline),
        current_label=_label(current),
        warnings=warnings,
        pass_rate=(_pass_rate(baseline["summary"]), _pass_rate(current["summary"])),
        field_accuracy={name: (before_fields.get(name), after_fields.get(name)) for name in names},
        regressions=regressions,
        fixes=fixes,
        status_changes=status_changes,
        only_in_baseline=sorted(before_status.keys() - after_status.keys()),
        only_in_current=sorted(after_status.keys() - before_status.keys()),
    )


def format_comparison(comparison):
    lines = [
        f"== Run comparison ({comparison.kind}) ==",
        f"Baseline: {comparison.baseline_label}",
        f"Current:  {comparison.current_label}",
    ]
    if comparison.warnings:
        lines += ["", "Warnings -- a score change may come from these, not only from model behavior:"]
        lines += [f"  {warning}" for warning in comparison.warnings]

    before, after = comparison.pass_rate
    lines += ["", f"Pass rate (errored excluded): {_percent(before)} -> {_percent(after)}{_delta(before, after)}"]

    if comparison.field_accuracy:
        lines += ["", "Per-field accuracy:"]
        for name, (was, now) in comparison.field_accuracy.items():
            lines.append(f"  {name:<16} {_counts(was)} -> {_counts(now)}{_delta(_ratio(was), _ratio(now))}")

    lines += ["", f"Regressions (passed -> failed): {len(comparison.regressions)}"]
    lines += [f"  {key}: {why}" for key, why in comparison.regressions]
    lines += [f"Fixes (failed -> passed): {len(comparison.fixes)}"]
    lines += [f"  {key}" for key in comparison.fixes]
    if comparison.status_changes:
        lines += ["Other status changes:"]
        lines += [f"  {key}: {was} -> {now}" for key, was, now in comparison.status_changes]
    if comparison.only_in_baseline:
        lines.append(f"Only in baseline: {', '.join(comparison.only_in_baseline)}")
    if comparison.only_in_current:
        lines.append(f"Only in current: {', '.join(comparison.only_in_current)}")
    return "\n".join(lines)


def _statuses(record):
    """{"source/example_id": (status, why it did not pass)} for every result in a run record."""
    statuses = {}
    for result in record["results"]:
        failing = [
            f"{f.get('label')}: expected {f.get('expected')!r}, got {f.get('actual')!r}"
            for f in result.get("fields") or [] if not f.get("passed")
        ]
        why = result.get("error") or "; ".join(failing)
        statuses[f"{result.get('source')}/{result.get('example_id')}"] = (result.get("status"), why)
    return statuses


def _label(record):
    return f"{record.get('started_at')} @ {record.get('git_commit') or 'unknown commit'}"


def _pass_rate(summary):
    graded = summary.get("total", 0) - summary.get("errored", 0)
    return summary.get("passed", 0) / graded if graded else None


def _ratio(counts):
    return counts[0] / counts[1] if counts and counts[1] else None


def _counts(counts):
    return f"{counts[0]}/{counts[1]} {_percent(_ratio(counts))}" if counts else "n/a"


def _percent(rate):
    return "n/a" if rate is None else f"{100 * rate:.1f}%"


def _delta(before, after):
    return "" if before is None or after is None else f"  ({100 * (after - before):+.1f} pts)"
```

- [ ] **Step 4: Run the comparison tests and confirm they pass**

Run: `python -m pytest tests/test_evals_runs.py -q`
Expected: all pass.

- [ ] **Step 5: Write the failing CLI tests**

In `tests/test_evals_runners.py`, add `import datetime` to the top-of-file imports and change the `evals` import line to `from evals import compare_runs, harness, judge, run_extraction_eval, run_matching_eval, runs`. Then append:

```python
# --- compare_runs CLI ---

def saved_matching_run(results_dir, started_at, status="passed"):
    results = [harness.ExampleResult("pooling", "pooling", status, [
        harness.FieldResult("match_existing", "candidate[0]", "TOPIC-0001",
                            "TOPIC-0001" if status == "passed" else "NEW: Pooling", status == "passed"),
    ])]
    record = runs.build_run_record(
        "matching", results, harness.aggregate(results), models={"matching": "m"}, prompts={"matching": "p"},
        golden="g", filter_name=None, started_at=started_at, commit=None,
    )
    return runs.save_run(record, results_dir)


T0 = datetime.datetime(2026, 9, 29, 9, 0, tzinfo=datetime.timezone.utc)


def test_compare_cli_latest_two_of_a_kind(tmp_path, capsys):
    saved_matching_run(tmp_path, T0)
    saved_matching_run(tmp_path, T0 + datetime.timedelta(hours=1), status="failed")

    code = compare_runs.main(["--kind", "matching", "--results-dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Regressions (passed -> failed): 1" in out
    assert "pooling/pooling: candidate[0]: expected 'TOPIC-0001', got 'NEW: Pooling'" in out


def test_compare_cli_pinned_baseline_against_latest(tmp_path, capsys):
    baseline = saved_matching_run(tmp_path, T0, status="failed")
    saved_matching_run(tmp_path, T0 + datetime.timedelta(hours=1), status="failed")
    saved_matching_run(tmp_path, T0 + datetime.timedelta(hours=2))

    code = compare_runs.main([str(baseline), "--results-dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Current:  2026-09-29T11:00:00+00:00" in out
    assert "Fixes (failed -> passed): 1" in out


def test_compare_cli_skips_junk_files_with_a_warning(tmp_path, capsys):
    saved_matching_run(tmp_path, T0)
    saved_matching_run(tmp_path, T0 + datetime.timedelta(hours=1))
    (tmp_path / "matching-half-written.json").write_text('{"schema_version": 1', encoding="utf-8")

    code = compare_runs.main(["--kind", "matching", "--results-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert code == 0
    assert "skipping unreadable run file" in captured.err and "matching-half-written.json" in captured.err


def test_compare_cli_exits_2_on_unusable_input(tmp_path, capsys):
    saved_matching_run(tmp_path, T0)
    junk = tmp_path / "junk.json"
    junk.write_text("{", encoding="utf-8")

    assert compare_runs.main(["--kind", "matching", "--results-dir", str(tmp_path)]) == 2
    assert "need at least two saved matching runs" in capsys.readouterr().err
    assert compare_runs.main(["--results-dir", str(tmp_path)]) == 2
    assert compare_runs.main([str(junk), str(junk)]) == 2
    assert "junk.json" in capsys.readouterr().err
```

- [ ] **Step 6: Run the CLI tests and confirm they fail**

Run: `python -m pytest tests/test_evals_runners.py -q`
Expected: collection error, `ImportError: cannot import name 'compare_runs' from 'evals'`.

- [ ] **Step 7: Create `evals/compare_runs.py`**

```python
"""Compare two saved eval runs to see what changed.

Usage:
  python evals/compare_runs.py --kind extraction                 # latest run vs the one before it
  python evals/compare_runs.py BASELINE.json                     # a pinned baseline vs the latest other run of its kind
  python evals/compare_runs.py BASELINE.json CURRENT.json

Reads the files the eval runners save under evals/results/. Makes no API calls.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals import runs  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare two saved eval runs.")
    parser.add_argument("run_files", nargs="*", metavar="RUN_FILE", help="baseline run file, then optionally the current one")
    parser.add_argument("--kind", choices=runs.KINDS, help="with no run files: compare the two latest runs of this kind")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR),
                        help="where the runners saved results (default: evals/results)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    if len(args.run_files) > 2:
        parser.error("give at most two run files")

    try:
        baseline_path, current_path = _pick(args)
        comparison = runs.compare_runs(runs.load_run(baseline_path), runs.load_run(current_path))
    except (runs.RunFileError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2

    print(f"baseline file: {baseline_path}")
    print(f"current file:  {current_path}")
    print()
    print(runs.format_comparison(comparison))
    return 0


def _pick(args):
    if len(args.run_files) == 2:
        return Path(args.run_files[0]), Path(args.run_files[1])
    if len(args.run_files) == 1:
        baseline_path = Path(args.run_files[0])
        kind = runs.load_run(baseline_path)["kind"]
    elif args.kind:
        baseline_path, kind = None, args.kind
    else:
        raise ValueError("give one or two run files, or --kind to compare the two latest runs of that kind")

    found, skipped = runs.find_runs(args.results_dir, kind)
    for path in skipped:
        print(f"warning: skipping unreadable run file {path}", file=sys.stderr)
    paths = [path for path, _ in found if baseline_path is None or path.resolve() != baseline_path.resolve()]

    if baseline_path is not None:
        if not paths:
            raise ValueError(f"no other saved {kind} run in {args.results_dir} to compare against")
        return baseline_path, paths[-1]
    if len(paths) < 2:
        raise ValueError(f"need at least two saved {kind} runs in {args.results_dir}, found {len(paths)}")
    return paths[-2], paths[-1]


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 8: Run the CLI tests and confirm they pass**

Run: `python -m pytest tests/test_evals_runners.py -q`
Expected: all pass. (`test_compare_cli_exits_2_on_unusable_input` writes `junk.json`, which does not match the `matching-*.json` pattern, so the single saved run stays alone and the first call reports "found 1".)

- [ ] **Step 9: Full suite + lint**

Run: `python -m pytest -q; ruff check .`
Expected: all pass, `All checks passed!`.

- [ ] **Step 10: Commit**

```bash
git add evals/runs.py evals/compare_runs.py tests/test_evals_runs.py tests/test_evals_runners.py
git commit -m "feat(evals): compare two saved runs with deltas, flips, and comparability warnings

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Judge calibration golden kind

**Files:**
- Modify: `evals/harness.py`
- Create: `evals/golden/judge/seed.yaml`
- Test: `tests/test_evals_harness.py` (append), `tests/test_evals_golden.py` (append)

**Interfaces:**
- Consumes: `judge.judge_summary(evidence, reference_summary, actual_summary, client) -> JudgeVerdict`, `judge.JudgeAPIError`, `judge.JudgeResponseError` (existing); harness helpers `_read_yaml`, `_fail`, `_require_str`, `_finish` (existing).
- Produces:
  - `harness.JUDGE_VERDICTS = ("pass", "fail")`
  - `harness.JudgeCase` dataclass: `id: str`, `source: str` (file stem), `evidence: dict` (`title`, `content`), `reference_summary: str`, `actual_summary: str`, `human_verdict: str`, `example_id: str | None = None`, `notes: str | None = None`
  - `harness.load_golden_dir(path, "judge") -> list[JudgeCase]` (new kind; duplicate ids across files rejected)
  - `harness.filter_examples(cases, name)` selects judge cases by file stem (like extraction)
  - `harness.grade_judge_case(case, client) -> ExampleResult`: one `FieldResult("verdict", "verdict", human, judge, agreed, detail=judge reason)`; `JudgeAPIError` → `errored`; `JudgeResponseError` → `failed` with `error`
  - `harness.JudgeAgreement` dataclass: `graded`, `agreed`, `false_pass`, `false_fail`, `unusable`, `human_pass`, `human_fail` (all `int`)
  - `harness.judge_agreement(results) -> JudgeAgreement`, `harness.format_judge_agreement(agreement) -> str`
  - Golden file format `evals/golden/judge/<name>.yaml`: top-level `cases:` list; each case has `id`, optional `example_id`, `evidence: {title, content}`, `reference_summary`, `actual_summary`, `human_verdict: pass|fail`, optional `notes`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_evals_harness.py`:

```python
# --- judge calibration cases ---

VALID_JUDGE = """
    cases:
      - id: jc-001
        example_id: db-001
        evidence:
          title: "Migration tool times out"
          content: "It hangs on 40M-row tables."
        reference_summary: "Migration tool times out on large tables."
        actual_summary: "Migrations hang because the server runs out of memory."
        human_verdict: fail
        notes: "Invents a cause."
      - id: jc-002
        evidence:
          title: "Migration tool times out"
          content: "It hangs on 40M-row tables."
        reference_summary: "Migration tool times out on large tables."
        actual_summary: "Migrations hang on very large tables."
        human_verdict: pass
"""


def test_load_judge_cases(tmp_path):
    write(tmp_path, "tuning.yaml", VALID_JUDGE)

    cases = harness.load_golden_dir(tmp_path, "judge")

    assert [c.id for c in cases] == ["jc-001", "jc-002"]
    first, second = cases
    assert first.source == "tuning"
    assert first.example_id == "db-001"
    assert first.evidence == {"title": "Migration tool times out", "content": "It hangs on 40M-row tables."}
    assert first.actual_summary == "Migrations hang because the server runs out of memory."
    assert first.human_verdict == "fail"
    assert first.notes == "Invents a cause."
    assert second.example_id is None and second.human_verdict == "pass"


@pytest.mark.parametrize("old, new, message", [
    ("human_verdict: fail", "human_verdict: null", "has not been labeled yet"),
    ("human_verdict: fail", "human_verdict: FAIL", "'human_verdict' must be 'pass' or 'fail'"),
    ('actual_summary: "Migrations hang because the server runs out of memory."', "actual_summary: ''",
     "'actual_summary' must be a non-empty string"),
    ("example_id: db-001", "example_id: 7", "'example_id' must be a string"),
])
def test_load_judge_cases_rejects_bad_cases_naming_file_and_id(tmp_path, old, new, message):
    write(tmp_path, "tuning.yaml", VALID_JUDGE.replace(old, new, 1))

    with pytest.raises(harness.GoldenSchemaError) as info:
        harness.load_golden_dir(tmp_path, "judge")

    assert "tuning.yaml [jc-001]" in str(info.value)
    assert message in str(info.value)


def test_load_judge_cases_rejects_missing_cases_list(tmp_path):
    write(tmp_path, "tuning.yaml", "cases: []\n")

    with pytest.raises(harness.GoldenSchemaError, match=r"tuning\.yaml.*'cases' must be a non-empty list"):
        harness.load_golden_dir(tmp_path, "judge")


def test_load_judge_cases_rejects_duplicate_ids_across_files(tmp_path):
    write(tmp_path, "a.yaml", VALID_JUDGE)
    write(tmp_path, "b.yaml", VALID_JUDGE)

    with pytest.raises(harness.GoldenSchemaError, match=r"b\.yaml \[jc-001\].*duplicate id.*a\.yaml"):
        harness.load_golden_dir(tmp_path, "judge")


def test_filter_examples_selects_judge_cases_by_file(tmp_path):
    write(tmp_path, "tuning.yaml", VALID_JUDGE)
    write(tmp_path, "holdout.yaml", VALID_JUDGE.replace("jc-00", "jh-00"))

    cases = harness.load_golden_dir(tmp_path, "judge")

    assert [c.id for c in harness.filter_examples(cases, "holdout")] == ["jh-001", "jh-002"]


JUDGE_CASE = harness.JudgeCase(
    id="jc-001", source="tuning",
    evidence={"title": "Migration tool times out", "content": "It hangs on 40M-row tables."},
    reference_summary="Migration tool times out on large tables.",
    actual_summary="Migrations hang because the server runs out of memory.",
    human_verdict="fail",
)


def test_grade_judge_case_agreement_passes_and_keeps_reason():
    result = harness.grade_judge_case(JUDGE_CASE, FakeClient([JUDGE_FAIL]))

    assert result.status == "passed"
    [verdict] = result.fields
    assert (verdict.field, verdict.expected, verdict.actual, verdict.detail) == (
        "verdict", "fail", "fail", "Adds a cause the post never states.",
    )


def test_grade_judge_case_disagreement_fails():
    result = harness.grade_judge_case(JUDGE_CASE, FakeClient([JUDGE_PASS]))

    assert result.status == "failed"
    assert (result.fields[0].expected, result.fields[0].actual) == ("fail", "pass")


def test_grade_judge_case_api_error_is_errored_and_unusable_output_is_failed():
    errored = harness.grade_judge_case(JUDGE_CASE, FakeClient([RuntimeError("529 overloaded")]))
    unusable = harness.grade_judge_case(JUDGE_CASE, FakeClient(["no json here"]))

    assert errored.status == "errored" and "529 overloaded" in errored.error
    assert unusable.status == "failed" and unusable.fields == [] and "non-JSON" in unusable.error


def verdict_result(case_id, human, judged):
    agreed = human == judged
    return harness.ExampleResult(case_id, "tuning", "passed" if agreed else "failed",
                                 [harness.FieldResult("verdict", "verdict", human, judged, agreed)])


def test_judge_agreement_counts_false_passes_false_fails_and_unusable():
    results = [
        verdict_result("a", "pass", "pass"),
        verdict_result("b", "fail", "fail"),
        verdict_result("c", "fail", "pass"),
        verdict_result("d", "pass", "fail"),
        verdict_result("e", "pass", "fail"),
        harness.ExampleResult("f", "tuning", "failed", error="judge returned non-JSON response"),
        harness.ExampleResult("g", "tuning", "errored", error="529"),
    ]

    assert harness.judge_agreement(results) == harness.JudgeAgreement(
        graded=6, agreed=2, false_pass=1, false_fail=2, unusable=1, human_pass=3, human_fail=2,
    )


def test_format_judge_agreement():
    text = harness.format_judge_agreement(harness.JudgeAgreement(6, 2, 1, 2, 1, 3, 2))

    assert "Judge agreement with human labels: 2/6 (33.3%)" in text
    assert "false pass (judge passed, human failed): 1 of 2 human-failed cases" in text
    assert "false fail (judge failed, human passed): 2 of 3 human-passed cases" in text
    assert "unusable judge output: 1" in text


def test_format_judge_agreement_with_nothing_graded():
    assert "0/0 (n/a)" in harness.format_judge_agreement(harness.judge_agreement([]))
```

Append to `tests/test_evals_golden.py`:

```python
def test_committed_judge_calibration_set_is_valid_and_labeled():
    cases = harness.load_golden_dir(GOLDEN_DIR / "judge", "judge")

    assert {c.human_verdict for c in cases} == {"pass", "fail"}
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_harness.py tests/test_evals_golden.py -q`
Expected: collection error, `AttributeError: module 'evals.harness' has no attribute 'JudgeCase'`.

- [ ] **Step 3: Add the judge-case kind to `evals/harness.py`**

3a. After `EXTRACTION_EXPECTED_KEYS = ...` add:

```python
JUDGE_VERDICTS = ("pass", "fail")
```

3b. After the `MatchingScenario` dataclass add:

```python
@dataclass
class JudgeCase:
    """A human-labeled judgment of one candidate summary, for measuring the summary judge itself."""
    id: str
    source: str
    evidence: dict
    reference_summary: str
    actual_summary: str
    human_verdict: str
    example_id: str | None = None
    notes: str | None = None
```

3c. After the `Report` dataclass add:

```python
@dataclass
class JudgeAgreement:
    graded: int
    agreed: int
    false_pass: int
    false_fail: int
    unusable: int
    human_pass: int
    human_fail: int
```

3d. In `load_golden_dir`, register the loader and key judge cases by id. Replace the docstring, the `loaders` line and the two lines that pick `key` / `what`:

```python
def load_golden_dir(path, kind):
    """Load and validate every .yaml/.yml file in `path`. `kind` is "extraction", "matching", or "judge"."""
    loaders = {"extraction": _load_extraction_file, "matching": _load_matching_file, "judge": _load_judge_file}
```

```python
            key = item.name if kind == "matching" else item.id
            if key in seen:
                what = "scenario name" if kind == "matching" else "id"
```

3e. Replace `filter_examples`:

```python
def filter_examples(items, name):
    """Keep extraction examples / judge cases whose golden file stem is `name`, or the matching scenario named `name`."""
    if name is None:
        return list(items)
    return [item for item in items if (item.name if isinstance(item, MatchingScenario) else item.source) == name]
```

3f. Pull the evidence parsing out of `_parse_extraction_example` so judge cases reuse it. Add this helper after `_require_str`:

```python
def _parse_evidence(file_path, item_id, raw):
    evidence = raw.get("evidence")
    if not isinstance(evidence, dict):
        _fail(file_path, item_id, "'evidence' must be a mapping")
    return {
        "title": _require_str(file_path, item_id, evidence, "title"),
        "content": _require_str(file_path, item_id, evidence, "content", allow_empty=True),
    }
```

and in `_parse_extraction_example` replace

```python
    evidence = raw.get("evidence")
    if not isinstance(evidence, dict):
        _fail(file_path, example_id, "'evidence' must be a mapping")
    evidence = {
        "title": _require_str(file_path, example_id, evidence, "title"),
        "content": _require_str(file_path, example_id, evidence, "content", allow_empty=True),
    }
```

with

```python
    evidence = _parse_evidence(file_path, example_id, raw)
```

3g. After `_load_matching_file` add:

```python
def _load_judge_file(file_path, data):
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        _fail(file_path, None, "'cases' must be a non-empty list")
    return [_parse_judge_case(file_path, position, raw) for position, raw in enumerate(cases)]


def _parse_judge_case(file_path, position, raw):
    if not isinstance(raw, dict):
        _fail(file_path, f"#{position}", "case must be a mapping")
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not case_id.strip():
        _fail(file_path, f"#{position}", "'id' must be a non-empty string")

    verdict = raw.get("human_verdict")
    if verdict not in JUDGE_VERDICTS:
        hint = " (null means the case has not been labeled yet)" if verdict is None else ""
        _fail(file_path, case_id, f"'human_verdict' must be 'pass' or 'fail', got {verdict!r}{hint}")
    example_id = raw.get("example_id")
    if example_id is not None and not isinstance(example_id, str):
        _fail(file_path, case_id, "'example_id' must be a string or omitted")

    return JudgeCase(
        id=case_id,
        source=file_path.stem,
        evidence=_parse_evidence(file_path, case_id, raw),
        reference_summary=_require_str(file_path, case_id, raw, "reference_summary").strip(),
        actual_summary=_require_str(file_path, case_id, raw, "actual_summary").strip(),
        human_verdict=verdict,
        example_id=example_id,
        notes=raw.get("notes"),
    )
```

3h. After `_is_complete_new_topic` (before `aggregate`) add:

```python
def grade_judge_case(case, client):
    try:
        verdict = judge.judge_summary(case.evidence, case.reference_summary, case.actual_summary, client)
    except judge.JudgeAPIError as e:
        return ExampleResult(case.id, case.source, "errored", error=str(e))
    except judge.JudgeResponseError as e:
        return ExampleResult(case.id, case.source, "failed", error=str(e))
    judged = "pass" if verdict.passed else "fail"
    return _finish(case.id, case.source, [FieldResult(
        "verdict", "verdict", case.human_verdict, judged, judged == case.human_verdict, detail=verdict.reason,
    )])


def judge_agreement(results):
    """Judge-vs-human agreement over graded (non-errored) cases. A case whose judge output could not
    be parsed counts as graded but not agreed."""
    counts = Counter()
    for result in results:
        if result.status == "errored":
            continue
        counts["graded"] += 1
        verdict = next((f for f in result.fields if f.field == "verdict"), None)
        if verdict is None:
            counts["unusable"] += 1
            continue
        counts[f"human_{verdict.expected}"] += 1
        if verdict.passed:
            counts["agreed"] += 1
        elif verdict.actual == "pass":
            counts["false_pass"] += 1
        else:
            counts["false_fail"] += 1
    return JudgeAgreement(**{name: counts[name] for name in (
        "graded", "agreed", "false_pass", "false_fail", "unusable", "human_pass", "human_fail",
    )})


def format_judge_agreement(agreement):
    return "\n".join([
        f"Judge agreement with human labels: {agreement.agreed}/{agreement.graded} "
        f"({_percent(agreement.agreed, agreement.graded)})",
        f"  false pass (judge passed, human failed): {agreement.false_pass} of {agreement.human_fail} human-failed cases",
        f"  false fail (judge failed, human passed): {agreement.false_fail} of {agreement.human_pass} human-passed cases",
        f"  unusable judge output: {agreement.unusable}",
    ])
```

- [ ] **Step 4: Create the seed calibration set `evals/golden/judge/seed.yaml`**

```yaml
# Seed judge-calibration cases: synthetic summaries written against the synthetic extraction seed
# examples (evals/golden/extraction/database_development.yaml) to exercise both verdicts in CI.
# Real calibration cases go in tuning.yaml / holdout.yaml -- see README "Tuning the judge rubric".
cases:
  - id: js-001
    example_id: db-001
    evidence:
      title: "Postgres migration tool keeps timing out on large tables"
      content: >
        Every time I try to run a migration on our biggest table (40M rows) it just
        hangs and eventually times out. No error message, just nothing.
    reference_summary: User reports the migration tool times out on large tables with no error message.
    actual_summary: Migrations hang and time out on a 40M-row table without showing any error.
    human_verdict: pass
    notes: Faithful paraphrase with more detail; every detail is in the post.

  - id: js-002
    example_id: db-001
    evidence:
      title: "Postgres migration tool keeps timing out on large tables"
      content: >
        Every time I try to run a migration on our biggest table (40M rows) it just
        hangs and eventually times out. No error message, just nothing.
    reference_summary: User reports the migration tool times out on large tables with no error message.
    actual_summary: Migrations time out on large tables because the tool loads the whole table into memory.
    human_verdict: fail
    notes: Invents a cause the post never states.

  - id: js-003
    example_id: db-003
    evidence:
      title: "Autocomplete should know about CTE columns"
      content: >
        When I write a WITH clause, the editor's autocomplete has no idea what columns
        the CTE returns, so I end up typing everything by hand. Would be a huge time saver
        if it could infer them.
    reference_summary: User wants the SQL editor's autocomplete to suggest columns defined in CTEs.
    actual_summary: Autocomplete should suggest the columns a WITH-clause CTE returns.
    human_verdict: pass
    notes: Same request, different wording.

  - id: js-004
    example_id: db-003
    evidence:
      title: "Autocomplete should know about CTE columns"
      content: >
        When I write a WITH clause, the editor's autocomplete has no idea what columns
        the CTE returns, so I end up typing everything by hand. Would be a huge time saver
        if it could infer them.
    reference_summary: User wants the SQL editor's autocomplete to suggest columns defined in CTEs.
    actual_summary: User has feedback about the SQL editor.
    human_verdict: fail
    notes: Too vague to act on.

  - id: js-005
    example_id: db-004
    evidence:
      title: "License price doubled at renewal"
      content: >
        Our team renewal quote came in at twice last year's price for the same five seats.
        Nothing new that we use was added. Hard to justify this to finance.
    reference_summary: User complains the renewal price doubled for the same seats without added value.
    actual_summary: Renewal quote doubled for the same five seats with no new features they use.
    human_verdict: pass
    notes: Faithful and specific.

  - id: js-006
    example_id: db-004
    evidence:
      title: "License price doubled at renewal"
      content: >
        Our team renewal quote came in at twice last year's price for the same five seats.
        Nothing new that we use was added. Hard to justify this to finance.
    reference_summary: User complains the renewal price doubled for the same seats without added value.
    actual_summary: Team is cancelling their license and moving to a competitor after the renewal price doubled.
    human_verdict: fail
    notes: Adds switching intent the post does not state.
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_evals_harness.py tests/test_evals_golden.py -q`
Expected: all pass, including every pre-existing extraction/matching loader test (the `_parse_evidence` refactor must not change their messages).

- [ ] **Step 6: Full suite + lint**

Run: `python -m pytest -q; ruff check .`
Expected: all pass, `All checks passed!`.

- [ ] **Step 7: Commit**

```bash
git add evals/harness.py evals/golden/judge/seed.yaml tests/test_evals_harness.py tests/test_evals_golden.py
git commit -m "feat(evals): judge calibration cases with human verdicts and agreement scoring

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Judge eval runner, case export, docs

**Files:**
- Create: `evals/run_judge_eval.py`, `evals/export_judge_cases.py`
- Modify: `README.md` (Evals section), `docs/superpowers/specs/2026-09-24-extraction-matching-evals-design.md` (Open items)
- Test: `tests/test_evals_runners.py` (append)

**Interfaces:**
- Consumes: `harness.load_golden_dir(..., "judge")`, `harness.filter_examples`, `harness.grade_judge_case`, `harness.judge_agreement`, `harness.format_judge_agreement` (Task 4); `runs.*` (Tasks 1-2); `harness.load_golden_dir(..., "extraction")` (existing).
- Produces:
  - `run_judge_eval.main(argv=None, client=None) -> int`: `[--filter FILE_STEM] [--model MODEL] [--no-save] [--results-dir DIR]`; saves kind `"judge"` with `models={"judge": ...}` and `prompts={"judge": ...}`.
  - `export_judge_cases.draft_cases(record: dict, examples: list[ExtractionExample]) -> tuple[list[dict], list[str]]`: (unlabeled case mappings, `"source/id"` keys whose golden example no longer exists).
  - `export_judge_cases.main(argv=None) -> int`: `RUN_FILE [--out PATH] [--force]`; default `--out` is `evals/results/judge_cases_draft.yaml`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_evals_runners.py`, add `import yaml` to the top-of-file imports and change the `evals` import line to `from evals import compare_runs, export_judge_cases, harness, judge, run_extraction_eval, run_judge_eval, run_matching_eval, runs`. Then append:

```python
# --- judge runner and case export ---

def test_judge_runner_reports_agreement_and_saves_run(tmp_path, capsys):
    client = RepeatingClient(json.dumps({"verdict": "pass", "reason": "Looks faithful."}))

    code = run_judge_eval.main(["--filter", "seed", "--results-dir", str(tmp_path)], client=client)

    out = capsys.readouterr().out
    assert code == 0
    assert "Judge agreement with human labels: 3/6 (50.0%)" in out
    assert "false pass (judge passed, human failed): 3 of 3 human-failed cases" in out
    [(_, record)], _ = runs.find_runs(tmp_path, "judge")
    assert record["models"] == {"judge": judge.JUDGE_MODEL}
    assert record["prompts"] == {"judge": runs.fingerprint(judge.JUDGE_PROMPT_TEMPLATE)}
    assert record["summary"]["field_accuracy"] == {"verdict": [3, 6]}


def test_judge_runner_unknown_filter_exits_2(tmp_path):
    assert run_judge_eval.main(["--filter", "nope", "--results-dir", str(tmp_path)], client=FakeClient([])) == 2


def extraction_run_file(tmp_path, results):
    record = runs.build_run_record(
        "extraction", results, harness.aggregate(results), models={}, prompts={}, golden="g",
        filter_name=None, started_at=T0, commit=None,
    )
    return runs.save_run(record, tmp_path)


SEED_EXAMPLES = harness.load_golden_dir(run_extraction_eval.GOLDEN_DIR, "extraction")
DB_001 = next(e for e in SEED_EXAMPLES if e.id == "db-001")


def test_draft_cases_takes_judged_summaries_and_reports_missing_examples(tmp_path):
    results = [
        harness.ExampleResult("db-001", "database_development", "failed", [
            harness.FieldResult("skip", "skip", False, False, True),
            harness.FieldResult("summary", "summary", "old ref", "Migrations time out on huge tables.", False, "Vague."),
        ]),
        harness.ExampleResult("db-002", "database_development", "passed", [harness.FieldResult("skip", "skip", True, True, True)]),
        harness.ExampleResult("gone-9", "database_development", "passed", [
            harness.FieldResult("summary", "summary", "r", "a", True, "ok"),
        ]),
    ]
    record = runs.load_run(extraction_run_file(tmp_path, results))

    cases, missing = export_judge_cases.draft_cases(record, SEED_EXAMPLES)

    assert missing == ["database_development/gone-9"]
    assert cases == [{
        "id": "jc-db-001",
        "example_id": "db-001",
        "evidence": DB_001.evidence,
        "reference_summary": DB_001.reference_summary,
        "actual_summary": "Migrations time out on huge tables.",
        "human_verdict": None,
        "notes": None,
    }]


def test_export_writes_a_draft_the_loader_accepts_once_labeled(tmp_path, capsys):
    run_file = extraction_run_file(tmp_path, [harness.ExampleResult("db-001", "database_development", "passed", [
        harness.FieldResult("summary", "summary", "r", "Migrations hang on a 40M-row table, no error shown.", True, "ok"),
    ])])
    out = tmp_path / "draft" / "cases.yaml"

    assert export_judge_cases.main([str(run_file), "--out", str(out)]) == 0
    assert "wrote 1 unlabeled cases" in capsys.readouterr().out

    labeled_dir = tmp_path / "labeled"
    labeled_dir.mkdir()
    text = out.read_text(encoding="utf-8").replace("human_verdict: null", "human_verdict: pass")
    (labeled_dir / "tuning.yaml").write_text(text, encoding="utf-8")
    [case] = harness.load_golden_dir(labeled_dir, "judge")
    assert (case.id, case.human_verdict) == ("jc-db-001", "pass")
    assert yaml.safe_load(out.read_text(encoding="utf-8"))["cases"][0]["human_verdict"] is None


def test_export_refuses_to_overwrite_and_rejects_non_extraction_runs(tmp_path, capsys):
    run_file = extraction_run_file(tmp_path, [harness.ExampleResult("db-001", "database_development", "passed", [
        harness.FieldResult("summary", "summary", "r", "a", True, "ok"),
    ])])
    out = tmp_path / "cases.yaml"
    out.write_text("keep me", encoding="utf-8")

    assert export_judge_cases.main([str(run_file), "--out", str(out)]) == 2
    assert out.read_text(encoding="utf-8") == "keep me"
    assert "--force" in capsys.readouterr().err

    matching_file = saved_matching_run(tmp_path, T0)
    assert export_judge_cases.main([str(matching_file), "--out", str(tmp_path / "x.yaml")]) == 2
    assert "extraction" in capsys.readouterr().err
```

- [ ] **Step 2: Run the tests and confirm they fail**

Run: `python -m pytest tests/test_evals_runners.py -q`
Expected: collection error, `ImportError: cannot import name 'export_judge_cases' from 'evals'`.

- [ ] **Step 3: Create `evals/run_judge_eval.py`**

```python
"""Measure the summary judge against human-labeled calibration cases.

Usage: python evals/run_judge_eval.py [--filter tuning] [--model MODEL] [--no-save]

Calls the real Anthropic API (one judge call per case). "Passed" here means the judge agreed
with the human label. Each run is saved as JSON under evals/results/; compare runs with
evals/compare_runs.py. This is a manual tool, not part of pytest or CI.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, judge, runs  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "judge")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Grade the summary judge against human-labeled cases.")
    parser.add_argument("--filter", help="only run the calibration file with this basename, e.g. tuning or holdout")
    parser.add_argument("--model", help=f"override judge.JUDGE_MODEL (default: {judge.JUDGE_MODEL})")
    parser.add_argument("--no-save", action="store_true", help="do not save this run to --results-dir")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR),
                        help="where to save the run (default: evals/results)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        cases = harness.load_golden_dir(GOLDEN_DIR, "judge")
    except harness.GoldenSchemaError as e:
        print(f"judge calibration set is malformed: {e}", file=sys.stderr)
        return 2

    selected = harness.filter_examples(cases, args.filter)
    if not selected:
        available = ", ".join(sorted({c.source for c in cases}))
        print(f"no calibration file named {args.filter!r}; available: {available}", file=sys.stderr)
        return 2

    if args.model:
        judge.JUDGE_MODEL = args.model

    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    print(f"judge model: {judge.JUDGE_MODEL}")
    results = []
    for number, case in enumerate(selected, 1):
        result = harness.grade_judge_case(case, client)
        print(f"[{number}/{len(selected)}] {case.source}/{case.id}: {result.status}")
        results.append(result)

    report = harness.aggregate(results)
    print()
    print(harness.format_report(report))
    print()
    print(harness.format_judge_agreement(harness.judge_agreement(results)))

    if not args.no_save:
        record = runs.build_run_record(
            "judge", results, report,
            models={"judge": judge.JUDGE_MODEL},
            prompts={"judge": runs.fingerprint(judge.JUDGE_PROMPT_TEMPLATE)},
            golden=runs.golden_fingerprint(selected),
            filter_name=args.filter, started_at=started_at, commit=runs.git_commit(),
        )
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Create `evals/export_judge_cases.py`**

```python
"""Draft judge-calibration cases from a saved extraction run.

Usage: python evals/export_judge_cases.py evals/results/extraction-<stamp>.json [--out PATH] [--force]

Writes every summary the judge graded in that run as an unlabeled case (human_verdict: null).
Label each case, then move it into evals/golden/judge/tuning.yaml or holdout.yaml; the loader
rejects unlabeled cases. Makes no API calls.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from evals import harness, runs  # noqa: E402

EXTRACTION_GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "extraction")

DRAFT_HEADER = """\
# Unlabeled judge-calibration cases drafted from an extraction run.
# For each case, decide pass/fail YOURSELF from the evidence and reference summary, then set
# human_verdict. Add hand-written failing variants (invented cause, side point, too vague,
# added sentiment) so the set has real fails. Then move cases into evals/golden/judge/tuning.yaml
# or holdout.yaml.
"""


def draft_cases(record, examples):
    """Unlabeled cases for every summary the judge graded in an extraction run record, plus the
    "source/id" keys whose golden example no longer exists (so there is no evidence to copy)."""
    by_key = {(e.source, e.id): e for e in examples}
    cases, missing = [], []
    for result in record["results"]:
        summary = next((f for f in result.get("fields") or [] if f.get("field") == "summary"), None)
        if summary is None:
            continue
        example = by_key.get((result.get("source"), result.get("example_id")))
        if example is None:
            missing.append(f"{result.get('source')}/{result.get('example_id')}")
            continue
        cases.append({
            "id": f"jc-{example.id}",
            "example_id": example.id,
            "evidence": dict(example.evidence),
            "reference_summary": example.reference_summary,
            "actual_summary": str(summary.get("actual")),
            "human_verdict": None,
            "notes": None,
        })
    return cases, missing


def main(argv=None):
    parser = argparse.ArgumentParser(description="Draft unlabeled judge-calibration cases from an extraction run.")
    parser.add_argument("run_file", help="a saved extraction run, e.g. evals/results/extraction-<stamp>.json")
    parser.add_argument("--out", default=str(runs.DEFAULT_RESULTS_DIR / "judge_cases_draft.yaml"),
                        help="where to write the draft (default: evals/results/judge_cases_draft.yaml)")
    parser.add_argument("--force", action="store_true", help="overwrite --out if it already exists")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        record = runs.load_run(args.run_file)
        if record["kind"] != "extraction":
            raise runs.RunFileError(f"{args.run_file}: is a {record['kind']} run; drafts come from extraction runs")
        examples = harness.load_golden_dir(EXTRACTION_GOLDEN_DIR, "extraction")
    except (runs.RunFileError, harness.GoldenSchemaError) as e:
        print(e, file=sys.stderr)
        return 2

    cases, missing = draft_cases(record, examples)
    for key in missing:
        print(f"warning: {key} is no longer in the golden set; skipped", file=sys.stderr)
    if not cases:
        print("that run has no judged summaries to draft cases from", file=sys.stderr)
        return 2

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} already exists; pass --force to overwrite it", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"cases": cases}, sort_keys=False, allow_unicode=True, width=100)
    out.write_text(DRAFT_HEADER + body, encoding="utf-8")
    print(f"wrote {len(cases)} unlabeled cases to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `python -m pytest tests/test_evals_runners.py -q`
Expected: all pass. (With the always-"pass" fake judge on the 6 seed cases, 3 human-pass cases agree and 3 human-fail cases are false passes.)

- [ ] **Step 6: Verify the new CLIs without touching the API**

Run: `python evals/run_judge_eval.py --filter nope; python evals/compare_runs.py --kind judge; python evals/export_judge_cases.py missing.json`
Expected: each prints a clear message to stderr and exits 2: the available calibration files (`seed`); `need at least two saved judge runs ... found 0`; `missing.json: no such run file`.

- [ ] **Step 7: Document in `README.md`**

In the Evals section, add `[--no-save]` to both runner usage lines. Then append this after the last existing bullet of the section:

````markdown
### Run history and drift

Every runner saves its run to `evals/results/<kind>-<UTC timestamp>.json` (gitignored) unless you pass `--no-save`. A run file records the models, a fingerprint of each prompt, a fingerprint of the golden data it graded, the `--filter`, and the git commit, next to every per-example result.

```powershell
python evals/compare_runs.py --kind extraction          # latest extraction run vs the one before it
python evals/compare_runs.py evals/results/extraction-20260929T101500Z.json   # pinned baseline vs latest
python evals/compare_runs.py BASELINE.json CURRENT.json
```

The comparison prints pass-rate and per-field deltas, examples that regressed (passed -> failed, with the reason) or were fixed, and examples present in only one run. When the model, a prompt, the golden data or the filter differs between the two runs, it prints a warning block first. Read the deltas as "the model changed its behavior" only when there are no warnings.

### Judge calibration

The summary judge is itself measured against human labels in `evals/golden/judge/*.yaml`. Each case holds evidence, a reference summary, a candidate summary, and `human_verdict: pass|fail`.

```powershell
python evals/export_judge_cases.py evals/results/extraction-<stamp>.json   # draft unlabeled cases from a real run
python evals/run_judge_eval.py [--filter tuning|holdout] [--model MODEL]
```

"Passed" in a judge run means the judge agreed with the human. The report adds false passes (judge accepted a summary the human rejected) and false fails. Label draft cases yourself before looking at what the judge said, add hand-written failing variants so the set contains real fails, and keep `holdout.yaml` out of sight while editing the rubric. Cases with `human_verdict: null` are rejected at load time.

### Tuning the judge rubric

Follow Task 6 of `docs/superpowers/plans/2026-09-29-eval-run-history-and-judge-tuning-implementation.md`: baseline agreement on `tuning` and `holdout`, edit `JUDGE_PROMPT_TEMPLATE` in `evals/judge.py` against the disagreements on `tuning` only, and keep a change only if it does not lose agreement on `holdout` and does not add false passes. After a rubric change, the next extraction comparison will warn `judge prompt changed`. That is expected: summary pass-rate movement across that boundary is the judge, not the extractor.
````

- [ ] **Step 8: Mark the open items resolved in the spec**

In `docs/superpowers/specs/2026-09-24-extraction-matching-evals-design.md`, under "Open items for implementation planning", replace the bullet that begins "Whether/how to persist run results" with:

```markdown
- Run persistence: resolved in `docs/superpowers/plans/2026-09-29-eval-run-history-and-judge-tuning-implementation.md`.
  Each run is saved as JSON under a gitignored `evals/results/`, and `evals/compare_runs.py` diffs two runs.
```

and replace the bullet "Exact wording of the judge rubric prompt in `judge.py`." with:

```markdown
- Judge rubric wording: v1 wording shipped. Changes are now measured against the human-labeled
  calibration set in `evals/golden/judge/` (same plan, Task 6).
```

Also, in the Non-goals section, change the sentence "v1 prints a report to the console; persisting results across runs to track pass-rate drift over time is a possible future addition, not part of this design." to: "v1 printed a report to the console only; pairwise run comparison was added later (see Open items), and a multi-run trend view is still out of scope."

- [ ] **Step 9: Full suite + lint**

Run: `python -m pytest -q; ruff check .`
Expected: all pass, `All checks passed!`.

- [ ] **Step 10: Commit**

```bash
git add evals/run_judge_eval.py evals/export_judge_cases.py tests/test_evals_runners.py README.md docs/superpowers/specs/2026-09-24-extraction-matching-evals-design.md
git commit -m "feat(evals): judge calibration runner, case export from real runs, and docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Tune the judge rubric against real output (manual, costs API credits)

This task needs a human in the loop and real API calls. **Only start it once the user has approved the API spend and the draft Reddit golden labels have been reviewed** (see Branching). Do it on its own branch from `main` after Tasks 1-5 are merged, e.g. `feat/evals-judge-rubric-v2`. The deliverable is either a rubric change backed by numbers, or a written "no change needed" result backed by numbers. Both are valid outcomes.

**Files:**
- Create: `evals/golden/judge/tuning.yaml`, `evals/golden/judge/holdout.yaml`
- Modify (only if the measurements justify it): `evals/judge.py` (`JUDGE_PROMPT_TEMPLATE`), `tests/test_evals_judge.py`

**Acceptance criteria for a rubric change** (all must hold, on 3 consecutive runs of each split, since the judge is non-deterministic):
1. `holdout` agreement is at least the baseline minimum and not below 85% on any run.
2. `holdout` false passes do not increase vs baseline. A false pass lets a bad extraction summary through, which is worse than a false fail.
3. `tuning` agreement improves over the baseline mean.
4. `python -m pytest -q; ruff check .` pass.

If criteria 1-2 already hold at baseline with ≥ 90% `holdout` agreement and no false passes, record the result and do not change the rubric.

- [ ] **Step 1: Baseline extraction run on the full golden set**

Run: `python evals/run_extraction_eval.py`
Expected: a report, then `saved run to evals/results/extraction-<stamp>.json`. Note the path.

- [ ] **Step 2: Draft calibration cases from that run**

Run: `python evals/export_judge_cases.py evals/results/extraction-<stamp>.json`
Expected: `wrote N unlabeled cases to evals/results/judge_cases_draft.yaml` (N ≈ number of non-skipped examples the model also didn't skip).

- [ ] **Step 3: Human labeling (user, not the agent)**

The user sets `human_verdict` on every drafted case against the rubric's intent (faithful, on-topic, useful to a PM), without first looking at the judge's verdict in the run file. Then they add hand-written failing variants until at least 1/3 of cases are `fail`. Real summaries mostly pass, and a set with no fails can't measure false passes. Write one variant per failure mode per a few examples: invented cause/number, side point instead of main point, too vague to act on, added sentiment or intent (e.g. switching intent), wrong product/entity. Give variants ids like `jc-rd-012-cause` and a `notes` line naming the failure mode. Target ≥ 45 cases.

The agent then splits them with a fixed rule so the split isn't hand-picked: sort by `id` and put every third case (positions 3, 6, 9, ...) into `holdout.yaml` and the rest into `tuning.yaml`. Both files use the `cases:` top-level key. Each needs a header comment saying real posts, human-labeled, and for holdout: "do not read while editing the rubric".

- [ ] **Step 4: Validate and commit the calibration set**

Run: `python -m pytest tests/test_evals_golden.py -q`
Expected: pass (it would fail on any unlabeled case).

```bash
git add evals/golden/judge/tuning.yaml evals/golden/judge/holdout.yaml
git commit -m "feat(evals): human-labeled judge calibration set from real extraction output

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Baseline judge agreement, 3 runs per split**

Run three times each: `python evals/run_judge_eval.py --filter tuning` and `python evals/run_judge_eval.py --filter holdout`
Record agreement %, false passes and false fails per run in a table (min/mean/max). These six saved runs are the baseline. Note the first `holdout` run's file path.

- [ ] **Step 6: Decide whether to change anything**

If the "no change" condition under the acceptance criteria holds, skip to Step 10. Otherwise continue.

- [ ] **Step 7: Diagnose disagreements on `tuning` only**

Read the "Failures" section of the `tuning` runs. Each disagreement shows the human label, the judge verdict and the judge's reason. Group them by cause. Examples: "judge fails summaries that add detail the post does state" (rubric too strict on detail), "judge passes summaries with added intent" (the faithful rule needs to name intent/sentiment explicitly), or "judge fails because wording differs from the reference" (on-topic rule too literal). Only disagreements seen in ≥ 2 cases, or in all 3 runs of one case, justify a rubric edit.

- [ ] **Step 8: Edit `JUDGE_PROMPT_TEMPLATE` for the top cause, one change at a time**

Change the smallest piece of rubric text that addresses the cause (usually one PASS/FAIL criterion sentence). Rules for the edit:
- Keep the placeholders `{title}`, `{content}`, `{reference_summary}`, `{actual_summary}` and the JSON response instructions (`"verdict"`, `"reason"`) unchanged, so `build_judge_prompt` and `parse_judge_response` keep working.
- Any literal `{` or `}` added to the template must be doubled (`{{`, `}}`), because the template goes through `str.format`.
- Do not copy calibration-case text into the rubric. That just teaches the judge the answers.

Then run `python -m pytest tests/test_evals_judge.py -q` (the existing tests assert the prompt still contains the evidence, reference and actual summary). Next, re-run `--filter tuning` 3 times. If `tuning` doesn't improve, revert the edit and try the next cause. Stop after 3 accepted edits or when `tuning` disagreements are only one-off cases.

- [ ] **Step 9: Check the held-out split**

Run `python evals/run_judge_eval.py --filter holdout` 3 times, then `python evals/compare_runs.py <baseline holdout run file>`. The comparison will warn `judge prompt changed: <old> -> <new>`. That is expected and is the variable under test. Check acceptance criteria 1-2 against the 3 runs. If they fail, revert the rubric to the last version that met them. Don't iterate on holdout failures, since that turns holdout into a second tuning set.

- [ ] **Step 10: Commit the result with the numbers**

If the rubric changed:

```bash
git add evals/judge.py
git commit -m "feat(evals): tighten judge rubric (<one-line summary of the rule change>)

Judge agreement with human labels, 3 runs each (min/mean/max):
  tuning:  <before> -> <after>
  holdout: <before> -> <after>; false passes <before> -> <after>

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

If nothing changed, add the baseline table to the PR description instead. Either way, run a fresh full extraction eval and `python evals/compare_runs.py --kind extraction` so the new baseline summary pass rate is on record. Put that comparison output (with its `judge prompt changed` warning, if any) in the PR description.
