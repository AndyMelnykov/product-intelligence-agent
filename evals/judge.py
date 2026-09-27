"""LLM-as-judge for the extraction eval's free-text summary field."""
from dataclasses import dataclass


class JudgeError(Exception):
    pass


class JudgeAPIError(JudgeError):
    """The judge API call itself failed -- the example is errored, not graded."""


class JudgeResponseError(JudgeError):
    """The judge responded, but its verdict could not be parsed."""


@dataclass
class JudgeVerdict:
    passed: bool
    reason: str
