import json

import extract
import match
from evals import judge, run_extraction_eval, run_matching_eval, runs


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
