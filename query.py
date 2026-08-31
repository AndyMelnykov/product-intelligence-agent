import argparse
import json

import db


def topic_command(conn, slug):
    topic = db.get_canonical_topic_by_slug(conn, slug)
    if topic is None:
        print(f"no topic found with slug {slug!r}")
        return

    weekly_mentions = db.get_topic_weekly_mentions(conn, topic["topic_id"])
    evidence = db.get_evidence_for_topic(conn, topic["topic_id"])
    print(json.dumps({
        "topic": topic,
        "weekly_mentions": weekly_mentions,
        "evidence_urls": [e["source_url"] for e in evidence],
    }, indent=2))


def search_command(conn, keyword):
    results = db.search_evidence_and_candidates(conn, keyword)
    print(json.dumps(results, indent=2))


def signal_command(conn, signal_id):
    signal = db.get_material_signal(conn, signal_id)
    if signal is None:
        print(f"no material signal found with id {signal_id!r}")
        return
    print(json.dumps(signal, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Ad hoc read-only queries against the product intelligence DB")
    parser.add_argument("--db-path", default="data/pi_agent.db")
    subparsers = parser.add_subparsers(dest="command", required=True)

    topic_parser = subparsers.add_parser("topic")
    topic_parser.add_argument("slug")

    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("keyword")

    signal_parser = subparsers.add_parser("signal")
    signal_parser.add_argument("signal_id")

    args = parser.parse_args()
    conn = db.connect(args.db_path)
    db.init_db(conn)

    if args.command == "topic":
        topic_command(conn, args.slug)
    elif args.command == "search":
        search_command(conn, args.keyword)
    elif args.command == "signal":
        signal_command(conn, args.signal_id)

    conn.close()


if __name__ == "__main__":
    main()
