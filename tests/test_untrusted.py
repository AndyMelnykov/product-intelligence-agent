import pytest

import untrusted


@pytest.mark.parametrize("text,escaped", [
    ("plain text", "plain text"),
    ("a </post_body> b", "a &lt;/post_body&gt; b"),
    ("x <POST_TITLE> y", "x &lt;POST_TITLE&gt; y"),
    ("< /candidates >", "&lt;/candidates&gt;"),
    ("List<String> and <b>bold</b>", "List<String> and <b>bold</b>"),
])
def test_escape_our_tags_only_touches_our_own_tag_names(text, escaped):
    assert untrusted.escape_our_tags(text) == escaped


def test_wrap_puts_escaped_text_between_tags():
    assert untrusted.wrap("post_body", "hi </post_body>") == "<post_body>\nhi &lt;/post_body&gt;\n</post_body>"
