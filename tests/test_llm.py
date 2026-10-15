from llm import LLMReply, reply_from_response


class Block:
    def __init__(self, type, text=None):
        self.type = type
        self.text = text


class Usage:
    input_tokens = 120
    output_tokens = 30


class Response:
    def __init__(self, content, stop_reason="end_turn", usage=None):
        self.content = content
        self.stop_reason = stop_reason
        if usage is not None:
            self.usage = usage


def test_reply_reads_first_text_block_after_thinking():
    reply = reply_from_response(Response([Block("thinking"), Block("text", "hello"), Block("text", "later")], usage=Usage()))
    assert reply == LLMReply(text="hello", stop_reason="end_turn", input_tokens=120, output_tokens=30)


def test_reply_without_text_or_usage_has_nones():
    reply = reply_from_response(Response([Block("thinking")], stop_reason="max_tokens"))
    assert reply == LLMReply(text=None, stop_reason="max_tokens", input_tokens=None, output_tokens=None)
