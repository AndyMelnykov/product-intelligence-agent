"""One fake Anthropic client for every test.

Every call is bound to the installed SDK's real `create` signature first, so a misspelled or
removed parameter fails the test instead of passing silently (checklist §3). The SDK classes are
imported at module top: if an upgrade moves them, the tests fail with an import error that names
the path, instead of quietly skipping the check.
"""
import inspect

from anthropic.resources import Messages as _SDKMessages
from anthropic.resources.beta import Messages as _SDKBetaMessages

_SIGNATURES = {False: inspect.signature(_SDKMessages.create), True: inspect.signature(_SDKBetaMessages.create)}


def check_sdk_call(kwargs, *, beta=False):
    """Raise TypeError when `kwargs` would not bind to the SDK's messages.create."""
    _SIGNATURES[beta].bind(None, **kwargs)


class _Checked:
    def __init__(self, messages, beta):
        self._messages = messages
        self._beta = beta

    def create(self, **kwargs):
        check_sdk_call(kwargs, beta=self._beta)
        return self._messages.create(**kwargs)

    def __getattr__(self, name):
        return getattr(self._messages, name)


def checked(messages, *, beta=False):
    """Wrap a hand-written messages fake so every create() is SDK-checked before it runs."""
    return _Checked(messages, beta)


class _Block:
    def __init__(self, type, text=None):
        self.type = type
        self.text = text
        self.thinking = ""


class _Response:
    def __init__(self, content, stop_reason, model):
        self.content = content
        self.stop_reason = stop_reason
        self.model = model


def text_response(text, *, stop_reason="end_turn", thinking_first=False, model="fake-model"):
    content = [_Block("text", text)]
    if thinking_first:
        content.insert(0, _Block("thinking"))
    return _Response(content, stop_reason, model)


class _Messages:
    def __init__(self, responses, calls, beta):
        self._responses = list(responses)
        self._calls = calls
        self._beta = beta

    def create(self, **kwargs):
        check_sdk_call(kwargs, beta=self._beta)
        self._calls.append(kwargs)
        result = self._responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        if isinstance(result, str):
            return text_response(result)
        return result


class _Beta:
    def __init__(self, messages):
        self.messages = messages


class FakeAnthropic:
    """Scripted client: each create() pops the next response (str -> text reply, exception -> raised)."""

    def __init__(self, responses, *, beta=False):
        self.calls = []
        messages = _Messages(responses, self.calls, beta)
        if beta:
            self.beta = _Beta(messages)
        else:
            self.messages = messages
