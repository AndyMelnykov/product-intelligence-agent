import dataclasses
import json
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


def test_grade_extraction_non_object_json_is_failed_not_crash():
    client = FakeClient(['[{"skip": true}]'])

    result = harness.grade_extraction_example(EXAMPLE, client)

    assert result.status == "failed"
    assert "expected a JSON object" in result.error


def test_grade_matching_unhashable_or_non_int_index_fails_without_crash():
    client = FakeClient([matcher_response(
        {"index": [0], "matched_topic_id": "TOPIC-0001", "new_topic": None},
        {"index": "1", "matched_topic_id": None, "new_topic": NEW_TOPIC},
    )])

    result = harness.grade_matching_scenario(SCENARIO, client)

    assert result.status == "failed"
    assert [f.actual for f in result.fields] == ["0 decisions for this index", "0 decisions for this index"]


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
