import argparse
import json

import db
import match


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


def health_command(conn):
    print(json.dumps(db.pipeline_health(conn, max_match_attempts=match.MAX_MATCH_ATTEMPTS), indent=2))


def calls_command(conn, stage, status, limit):
    print(json.dumps(db.list_llm_calls(conn, stage=stage, status=status, limit=limit), indent=2))


def usage_command(conn, since):
    print(json.dumps(db.llm_usage(conn, since=since), indent=2))


def triage_audit_command(conn):
    print(json.dumps(db.triage_audit(conn), indent=2))


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

    subparsers.add_parser("health", help="counts of pending, stuck, dead and unflushed work")

    calls_parser = subparsers.add_parser("calls", help="recent LLM calls from the ledger")
    calls_parser.add_argument("--stage")
    calls_parser.add_argument("--status")
    calls_parser.add_argument("--limit", type=int, default=20)

    usage_parser = subparsers.add_parser("usage", help="LLM calls and tokens per stage and model")
    usage_parser.add_argument("--since", help="ISO date, e.g. 2026-10-01")

    subparsers.add_parser("triage-audit", help="how often triage skips were wrong, from the 5% audit sample")

    args = parser.parse_args()
    conn = db.connect(args.db_path)
    db.init_db(conn)

    if args.command == "topic":
        topic_command(conn, args.slug)
    elif args.command == "search":
        search_command(conn, args.keyword)
    elif args.command == "signal":
        signal_command(conn, args.signal_id)
    elif args.command == "health":
        health_command(conn)
    elif args.command == "calls":
        calls_command(conn, args.stage, args.status, args.limit)
    elif args.command == "usage":
        usage_command(conn, args.since)
    elif args.command == "triage-audit":
        triage_audit_command(conn)

    conn.close()


if __name__ == "__main__":
    main()
