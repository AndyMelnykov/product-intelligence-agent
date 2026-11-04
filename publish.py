import re
from datetime import date

import db
import weekutil

SUMMARY_MAX = 300
DETAIL_MAX = 2000
REDDIT_ORIGIN = "https://www.reddit.com"

_USERNAME = re.compile(r"(?<![\w/])/?u/[A-Za-z0-9_-]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


class PublishError(Exception):
    pass


def material_topic_ids(conn, week: str) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT topic_id FROM material_signal WHERE week = ? AND topic_id IS NOT NULL ORDER BY topic_id",
        (week,),
    ).fetchall()
    return [r["topic_id"] for r in rows]


def scrub(text: str) -> str:
    """Replace Reddit usernames and email addresses; post bodies can still contain them."""
    return _EMAIL.sub("[email]", _USERNAME.sub("u/[user]", text))


def _summary(text: str) -> str:
    summary = " ".join(scrub(text).split())
    if len(summary) > SUMMARY_MAX:
        summary = summary[: SUMMARY_MAX - 1].rstrip() + "…"
    return summary


def build_signal(candidate: dict, slug: str) -> dict:
    source_ref = candidate["source_url"]
    if source_ref.startswith("/"):
        source_ref = REDDIT_ORIGIN + source_ref
    detail = scrub(f"{candidate['title'] or ''}\n\n{candidate['content'] or ''}").strip()
    return {
        "date": candidate["published_at"][:10],
        "source_type": "community",
        "source_ref": source_ref,
        "summary": _summary(candidate["summary"]),
        "detail": detail[:DETAIL_MAX],
        "tags": f"{slug};{candidate['signal_type']}",
    }


def collect_signals(conn, week: str, max_signals_per_topic: int) -> list[dict]:
    signals = []
    for topic_id in material_topic_ids(conn, week):
        topic = db.get_canonical_topic(conn, topic_id)
        if topic is None:
            continue
        in_week = [
            c for c in db.get_publishable_candidates(conn, topic_id)
            if weekutil.iso_week_string(date.fromisoformat(c["captured_at"][:10])) == week
        ]
        signals.extend(build_signal(c, topic["slug"]) for c in in_week[:max_signals_per_topic])
    return signals
