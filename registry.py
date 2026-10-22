"""Human operations on the canonical topic registry: merge, split, history and as-of views.

The pipeline only creates topics and matches candidates to them. Merges and splits happen here,
on purpose, by a person (or a person accepting a review item, P5). Every operation keeps history:
topic_version for definitions, topic_lineage for merges and splits, original_topic_id on candidates.
"""
import db
import match


class RegistryError(Exception):
    pass


def _active_topic(conn, slug):
    topic = db.get_canonical_topic_by_slug(conn, slug)
    if topic is None:
        raise RegistryError(f"no topic with slug {slug!r}")
    if topic["status"] != "active":
        raise RegistryError(f"topic {slug!r} is already merged into {topic['merged_into']}")
    return topic


def merge_topics(conn, from_slug, into_slug, *, week, reason, decided_by):
    """Retire from_slug into into_slug. Does not commit."""
    if from_slug == into_slug:
        raise RegistryError("cannot merge a topic into itself")
    source, target = _active_topic(conn, from_slug), _active_topic(conn, into_slug)

    moved = db.move_candidates(conn, source["topic_id"], target["topic_id"])
    db.set_topic_status(conn, source["topic_id"], "merged", merged_into=target["topic_id"])

    aliases = list(target["aliases"])
    for alias in [source["name"], *source["aliases"]]:
        if alias != target["name"] and alias not in aliases:
            aliases.append(alias)
    db.update_topic_definition(conn, target["topic_id"], week=week, reason=f"merged {source['topic_id']}", aliases=aliases)
    db.extend_topic_seen(conn, target["topic_id"], source["first_seen"])
    db.extend_topic_seen(conn, target["topic_id"], source["last_seen"])

    db.insert_topic_lineage(conn, kind="merge", from_topic_id=source["topic_id"], to_topic_id=target["topic_id"],
                            week=week, reason=reason, decided_by=decided_by, candidate_ids=moved)
    db.refresh_topic_weekly_mentions(conn, [source["topic_id"], target["topic_id"]])
    return {"from": source["topic_id"], "into": target["topic_id"], "moved": moved}


def split_topic(conn, slug, *, new_name, new_slug, new_description, candidate_ids, week, reason, decided_by):
    """Move candidate_ids out of `slug` into a new topic. Does not commit."""
    source = _active_topic(conn, slug)
    slug_new = match.normalize_slug(new_slug) or match.normalize_slug(new_name)
    if not slug_new:
        raise RegistryError("the new topic needs a usable slug")
    if db.get_canonical_topic_by_slug(conn, slug_new) is not None:
        raise RegistryError(f"slug {slug_new!r} already exists")
    if not candidate_ids:
        raise RegistryError("name at least one candidate to move")

    candidates = db.get_candidates_by_ids(conn, list(candidate_ids))
    found = {c["candidate_id"] for c in candidates if c["topic_id"] == source["topic_id"]}
    missing = [c for c in candidate_ids if c not in found]
    if missing:
        raise RegistryError(f"candidates not in topic {slug!r}: {missing}")
    total = conn.execute("SELECT COUNT(*) FROM signal_candidate WHERE topic_id = ?", (source["topic_id"],)).fetchone()[0]
    if len(found) == total:
        raise RegistryError("moving every candidate is a rename, not a split; edit the topic definition instead")

    weeks = sorted(match.candidate_week(c, week) for c in candidates)
    new_id = db.insert_canonical_topic(conn, slug=slug_new, name=new_name, description=new_description, aliases=[],
                                       first_seen=weeks[0], last_seen=weeks[-1], valid_from_week=week)
    moved = db.move_candidates(conn, source["topic_id"], new_id, list(candidate_ids))
    db.insert_topic_lineage(conn, kind="split", from_topic_id=source["topic_id"], to_topic_id=new_id,
                            week=week, reason=reason, decided_by=decided_by, candidate_ids=moved)
    db.refresh_topic_weekly_mentions(conn, [source["topic_id"], new_id])
    return {"from": source["topic_id"], "new": new_id, "moved": moved}
