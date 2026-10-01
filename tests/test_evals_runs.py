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
