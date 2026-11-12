import pytest

from anthropic_fake import FakeAnthropic, checked


def test_fake_rejects_a_parameter_the_sdk_does_not_accept():
    with pytest.raises(TypeError):
        FakeAnthropic(["x"]).messages.create(model="m", max_tokens=1, messages=[], tempurature=0)


def test_fake_records_calls_and_returns_scripted_text():
    client = FakeAnthropic(["x"])

    response = client.messages.create(model="m", max_tokens=1, messages=[])

    assert response.content[0].text == "x"
    assert client.calls == [{"model": "m", "max_tokens": 1, "messages": []}]


def test_beta_fake_accepts_betas_and_fallbacks():
    client = FakeAnthropic(["x"], beta=True)

    client.beta.messages.create(model="m", max_tokens=1, messages=[], betas=["b"], fallbacks="default")


class Echo:
    def __init__(self):
        self.seen = []

    def create(self, **kwargs):
        self.seen.append(kwargs)
        return "reply"


def test_checked_wrapper_rejects_a_bad_parameter_before_delegating():
    messages = checked(Echo())

    with pytest.raises(TypeError):
        messages.create(model="m", max_tokens=1, messages=[], tempurature=0)
    assert messages.seen == []


def test_checked_wrapper_delegates_calls_and_attributes():
    messages = checked(Echo())

    assert messages.create(model="m", max_tokens=1, messages=[]) == "reply"
    assert messages.seen == [{"model": "m", "max_tokens": 1, "messages": []}]
