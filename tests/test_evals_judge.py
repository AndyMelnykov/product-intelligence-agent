import json

import pytest

from evals import judge


class FakeContentBlock:
    type = "text"

    def __init__(self, text):
        self.text = text


class FakeThinkingBlock:
    type = "thinking"
    thinking = ""


class FakeResponse:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [FakeContentBlock(text)]
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, FakeResponse):
            return result
        return FakeResponse(result)


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessages(responses)


EVIDENCE = {"title": "Migration tool times out", "content": "It hangs on 40M-row tables, no error."}
REFERENCE = "Migration tool times out on large tables with no error message."
ACTUAL = "Migrations hang on very large tables without an error."


def test_judge_summary_returns_pass_verdict_and_sends_prompt():
    client = FakeClient([json.dumps({"verdict": "pass", "reason": "Faithful and on-topic."})])

    verdict = judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)

    assert verdict == judge.JudgeVerdict(passed=True, reason="Faithful and on-topic.")
    call = client.messages.calls[0]
    assert call["model"] == judge.JUDGE_MODEL
    prompt = call["messages"][0]["content"]
    for text in (EVIDENCE["title"], EVIDENCE["content"], REFERENCE, ACTUAL):
        assert text in prompt


def test_judge_summary_returns_fail_verdict():
    client = FakeClient([json.dumps({"verdict": "fail", "reason": "Invents a cause."})])

    verdict = judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)

    assert verdict == judge.JudgeVerdict(passed=False, reason="Invents a cause.")


def test_judge_summary_reads_text_block_after_thinking_block():
    response = FakeResponse(json.dumps({"verdict": "pass", "reason": "ok"}))
    response.content.insert(0, FakeThinkingBlock())
    client = FakeClient([response])

    assert judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client).passed is True


def test_judge_summary_wraps_api_failure():
    client = FakeClient([RuntimeError("529 overloaded")])

    with pytest.raises(judge.JudgeAPIError, match="529 overloaded"):
        judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_judge_summary_rejects_bad_stop_reason(stop_reason):
    client = FakeClient([FakeResponse("{}", stop_reason=stop_reason)])

    with pytest.raises(judge.JudgeResponseError, match=stop_reason):
        judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)


def test_judge_summary_rejects_response_without_text_block():
    response = FakeResponse("unused")
    response.content = [FakeThinkingBlock()]
    client = FakeClient([response])

    with pytest.raises(judge.JudgeResponseError, match="no text block"):
        judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)


def test_parse_judge_response_accepts_code_fence_and_case():
    raw = '```json\n{"verdict": "PASS", "reason": "  fine  "}\n```'

    assert judge.parse_judge_response(raw) == judge.JudgeVerdict(passed=True, reason="fine")


@pytest.mark.parametrize("raw", [
    "The summary looks good.",
    json.dumps(["pass"]),
    json.dumps({"verdict": "maybe", "reason": "?"}),
    json.dumps({"reason": "no verdict"}),
])
def test_parse_judge_response_rejects_unusable_output(raw):
    with pytest.raises(judge.JudgeResponseError):
        judge.parse_judge_response(raw)


def test_parse_judge_response_tolerates_missing_reason():
    assert judge.parse_judge_response(json.dumps({"verdict": "fail"})) == judge.JudgeVerdict(passed=False, reason="")
