import json

import pytest

from anthropic_fake import FakeAnthropic as FakeClient
from anthropic_fake import text_response
from evals import judge


EVIDENCE = {"title": "Migration tool times out", "content": "It hangs on 40M-row tables, no error."}
REFERENCE = "Migration tool times out on large tables with no error message."
ACTUAL = "Migrations hang on very large tables without an error."


def test_judge_summary_returns_pass_verdict_and_sends_prompt():
    client = FakeClient([json.dumps({"verdict": "pass", "reason": "Faithful and on-topic."})])

    verdict = judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)

    assert verdict == judge.JudgeVerdict(passed=True, reason="Faithful and on-topic.")
    call = client.calls[0]
    assert call["model"] == judge.JUDGE_MODEL
    prompt = call["messages"][0]["content"]
    for text in (EVIDENCE["title"], EVIDENCE["content"], REFERENCE, ACTUAL):
        assert text in prompt


def test_judge_summary_returns_fail_verdict():
    client = FakeClient([json.dumps({"verdict": "fail", "reason": "Invents a cause."})])

    verdict = judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)

    assert verdict == judge.JudgeVerdict(passed=False, reason="Invents a cause.")


def test_judge_summary_reads_text_block_after_thinking_block():
    response = text_response(json.dumps({"verdict": "pass", "reason": "ok"}), thinking_first=True)
    client = FakeClient([response])

    assert judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client).passed is True


def test_judge_summary_wraps_api_failure():
    client = FakeClient([RuntimeError("529 overloaded")])

    with pytest.raises(judge.JudgeAPIError, match="529 overloaded"):
        judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_judge_summary_rejects_bad_stop_reason(stop_reason):
    client = FakeClient([text_response("{}", stop_reason=stop_reason)])

    with pytest.raises(judge.JudgeResponseError, match=stop_reason):
        judge.judge_summary(EVIDENCE, REFERENCE, ACTUAL, client)


def test_judge_summary_rejects_response_without_text_block():
    response = text_response("unused", thinking_first=True)
    response.content.pop()  # leave only the thinking block
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
