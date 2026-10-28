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
