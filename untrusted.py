"""Mark untrusted text (Reddit posts, and model output derived from them) as data inside prompts.

Prompts put such text between our own tags and say it is data. To stop a post from closing the
block it sits in, any of OUR tag names inside the text is escaped. Other angle brackets (code,
HTML) are left alone.
"""
import re

OUR_TAGS = ("post_title", "post_body", "candidates", "focus_topics", "registry")
_OUR_TAG_RE = re.compile(r"<\s*(/?)\s*(" + "|".join(OUR_TAGS) + r")\s*>", re.IGNORECASE)


def escape_our_tags(text):
    return _OUR_TAG_RE.sub(lambda m: f"&lt;{m.group(1)}{m.group(2)}&gt;", text)


def wrap(tag, text):
    return f"<{tag}>\n{escape_our_tags(text)}\n</{tag}>"
