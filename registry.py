"""Human operations on the canonical topic registry: merge, split, history and as-of views.

The pipeline only creates topics and matches candidates to them. Merges and splits happen here,
on purpose, by a person (or a person accepting a review item, P5). Every operation keeps history:
topic_version for definitions, topic_lineage for merges and splits, original_topic_id on candidates.
"""
import argparse
import json
import sys
from datetime import date

import db
import match
from fingerprints import fingerprint
from weekutil import iso_week_string


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


def registry_as_of(conn, week):
    """The registry as it stood during `week`: topics that existed then and were not yet merged
    away, each with the definition that was valid that week."""
    merged_in_week = {r["from_topic_id"]: r["week"] for r in conn.execute(
        "SELECT from_topic_id, week FROM topic_lineage WHERE kind = 'merge'")}
    topics = []
    for topic in db.get_canonical_topics(conn, include_merged=True):
        if merged_in_week.get(topic["topic_id"], "9999-W99") <= week:
            continue
        version = db.get_topic_version_as_of(conn, topic["topic_id"], week)
        if version is None:
            continue
        topics.append({"topic_id": topic["topic_id"], "slug": topic["slug"], "name": version["name"],
                       "description": version["description"], "aliases": version["aliases"]})
    return topics


def registry_fingerprint(topics):
    return fingerprint(sorted(
        [t["topic_id"], t["name"], t["description"], list(t.get("aliases") or [])] for t in topics
    ))


def topic_history(conn, slug):
    topic = db.get_canonical_topic_by_slug(conn, slug)
    if topic is None:
        raise RegistryError(f"no topic with slug {slug!r}")
    return {
        "topic": topic,
        "versions": db.get_topic_versions(conn, topic["topic_id"]),
        "lineage": db.get_lineage_for_topic(conn, topic["topic_id"]),
        "weekly_mentions": db.get_topic_weekly_mentions(conn, topic["topic_id"]),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Inspect and edit the canonical topic registry.")
    parser.add_argument("--db-path", default="data/pi_agent.db")
    sub = parser.add_subparsers(dest="command", required=True)

    history = sub.add_parser("history", help="versions, merges/splits and weekly mentions of a topic")
    history.add_argument("slug")

    merge = sub.add_parser("merge", help="retire FROM into INTO")
    merge.add_argument("from_slug")
    merge.add_argument("into_slug")
    merge.add_argument("--reason", required=True)
    merge.add_argument("--by", default="human:cli")

    split = sub.add_parser("split", help="move some candidates of SLUG into a new topic")
    split.add_argument("slug")
    split.add_argument("--new-name", required=True)
    split.add_argument("--new-slug", required=True)
    split.add_argument("--description", required=True)
    split.add_argument("--candidates", required=True, help="comma-separated candidate ids, e.g. SC-2026-00012,SC-2026-00031")
    split.add_argument("--reason", required=True)
    split.add_argument("--by", default="human:cli")

    snapshot = sub.add_parser("snapshot", help="the registry as it stood in a given ISO week")
    snapshot.add_argument("--week", required=True, help="e.g. 2026-W40")

    rebuild = sub.add_parser("rebuild-mentions", help="recompute weekly mention counts from evidence")
    rebuild.add_argument("--apply", action="store_true", help="write the new counts (default: show what would change)")

    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    conn = db.connect(args.db_path)
    db.init_db(conn)
    week = iso_week_string(date.today())
    try:
        if args.command == "history":
            print(json.dumps(topic_history(conn, args.slug), indent=2))
        elif args.command == "merge":
            result = merge_topics(conn, args.from_slug, args.into_slug, week=week, reason=args.reason, decided_by=args.by)
            conn.commit()
            print(json.dumps(result, indent=2))
        elif args.command == "split":
            ids = [c.strip() for c in args.candidates.split(",") if c.strip()]
            result = split_topic(conn, args.slug, new_name=args.new_name, new_slug=args.new_slug,
                                 new_description=args.description, candidate_ids=ids, week=week,
                                 reason=args.reason, decided_by=args.by)
            conn.commit()
            print(json.dumps(result, indent=2))
        elif args.command == "snapshot":
            print(json.dumps(registry_as_of(conn, args.week), indent=2))
        elif args.command == "rebuild-mentions":
            diffs = db.rebuild_all_topic_weekly_mentions(conn)
            for topic_id, (before, after) in diffs.items():
                print(f"{topic_id}: {before} -> {after}")
            if args.apply:
                conn.commit()
                print(f"changed {len(diffs)} topic(s)")
            else:
                conn.rollback()
                print(f"would change {len(diffs)} topic(s); rerun with --apply to write")
    except RegistryError as e:
        conn.rollback()
        print(e, file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
