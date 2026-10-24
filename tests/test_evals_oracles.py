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
