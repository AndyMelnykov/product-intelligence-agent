import pytest

import match

TOPICS = [{"topic_id": "TOPIC-0001", "slug": "dark-mode-support", "name": "Dark mode", "description": "d"}]
CANDS = [{"candidate_id": f"SC-2026-0000{i}"} for i in range(1, 4)]


def actions(resolutions):
    return [(r.action, r.topic_id, r.new_topic["slug"] if r.new_topic else None) for r in resolutions]


@pytest.mark.parametrize("text,slug", [
    ("Export to CSV", "export-to-csv"),
    ("  --Weird__Slug!! ", "weird-slug"),
    ("export-to-csv", "export-to-csv"),
    (None, ""),
    ("!!!", ""),
])
def test_normalize_slug(text, slug):
    assert match.normalize_slug(text) == slug


def test_valid_match_and_create():
    decisions = [
        {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": None},
        {"index": 1, "matched_topic_id": None,
         "new_topic": {"name": "Export to CSV", "slug": "export-to-csv", "description": "wants CSV"}},
    ]
    assert actions(match.resolve_decisions(CANDS[:2], decisions, TOPICS)) == [
        ("match", "TOPIC-0001", None), ("create", None, "export-to-csv"),
    ]


def test_missing_and_duplicate_decisions_are_invalid():
    decisions = [{"index": 1, "matched_topic_id": "TOPIC-0001"}, {"index": 1, "matched_topic_id": "TOPIC-0001"}]
    resolutions = match.resolve_decisions(CANDS[:2], decisions, TOPICS)
    assert [(r.action, r.reason) for r in resolutions] == [("invalid", "no decision"), ("invalid", "2 decisions")]


def test_unknown_topic_id_is_invalid():
    [r] = match.resolve_decisions(CANDS[:1], [{"index": 0, "matched_topic_id": "TOPIC-9999"}], TOPICS)
    assert (r.action, r.reason) == ("invalid", "unknown topic id 'TOPIC-9999'")


def test_both_fields_set_is_invalid():
    decision = {"index": 0, "matched_topic_id": "TOPIC-0001", "new_topic": {"name": "x", "slug": "x", "description": "y"}}
    [r] = match.resolve_decisions(CANDS[:1], [decision], TOPICS)
    assert r.action == "invalid"


def test_new_topic_with_existing_slug_becomes_a_match():
    decision = {"index": 0, "matched_topic_id": None,
                "new_topic": {"name": "Dark Mode Support", "slug": "Dark-Mode-Support", "description": "d"}}
    [r] = match.resolve_decisions(CANDS[:1], [decision], TOPICS)
    assert (r.action, r.topic_id) == ("match", "TOPIC-0001")


def test_new_topic_slug_falls_back_to_name():
    decision = {"index": 0, "matched_topic_id": None, "new_topic": {"name": "Export to CSV", "slug": "", "description": "d"}}
    [r] = match.resolve_decisions(CANDS[:1], [decision], TOPICS)
    assert r.new_topic == {"name": "Export to CSV", "slug": "export-to-csv", "description": "d"}


@pytest.mark.parametrize("decisions", [None, "nope", {"index": 0}, [1, 2, 3], [{"index": "0"}], [{"index": True}]])
def test_non_list_or_garbage_output_never_raises(decisions):
    resolutions = match.resolve_decisions(CANDS, decisions, TOPICS)
    assert [r.action for r in resolutions] == ["invalid"] * 3
