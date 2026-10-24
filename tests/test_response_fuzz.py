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
