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
