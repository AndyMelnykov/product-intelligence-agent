"""One way to read an Anthropic Messages API response, shared by every model-calling stage."""
from dataclasses import dataclass


@dataclass(frozen=True)
class LLMReply:
    text: str | None
    stop_reason: str | None
    input_tokens: int | None
    output_tokens: int | None


def reply_from_response(response):
    # Sonnet 5 and Opus 5 run adaptive thinking by default, so a thinking block may precede the answer.
    text = next((block.text for block in response.content if getattr(block, "type", None) == "text"), None)
    usage = getattr(response, "usage", None)
    return LLMReply(
        text=text,
        stop_reason=getattr(response, "stop_reason", None),
        input_tokens=getattr(usage, "input_tokens", None),
        output_tokens=getattr(usage, "output_tokens", None),
    )
