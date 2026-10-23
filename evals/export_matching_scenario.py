"""Draft a matching-eval scenario from one production week.

Usage: python evals/export_matching_scenario.py --week 2026-W40 [--db-path data/pi_agent.db] [--limit 30] [--out PATH] [--force]

existing_topics = the registry as it stood the week before; candidates = that week's matched
candidates; expected = where production first put each one (original_topic_id), or a new topic
when that topic did not exist yet. Production is not ground truth: the file is written with
`draft: true`, which the golden loader refuses until a human has checked every decision and
deleted that line. Summaries come from real posts; read them before committing. Makes no API calls.
"""
import argparse
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

import db  # noqa: E402
import match  # noqa: E402
import registry  # noqa: E402
from evals import runs  # noqa: E402
from weekutil import iso_week_string  # noqa: E402

HEADER = """\
# DRAFT matching scenario exported from production. Check every expected decision: production
# is what the matcher did, not what is correct. Fix wrong ones, add notes, then delete the
# `draft: true` line and move the file into evals/golden/matching/.
"""


def previous_week(week):
    year, number = week.split("-W")
    monday = datetime.date.fromisocalendar(int(year), int(number), 1)
    return iso_week_string(monday - datetime.timedelta(days=7))


def build_scenario(conn, week, limit):
    topics = registry.registry_as_of(conn, previous_week(week))
    known = {t["topic_id"] for t in topics}
    rows = conn.execute(
        "SELECT sc.candidate_id, sc.signal_type, sc.summary, sc.original_topic_id, e.captured_at "
        "FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id "
        "WHERE sc.original_topic_id IS NOT NULL AND e.duplicate_of IS NULL ORDER BY sc.candidate_id"
    ).fetchall()
    chosen = [dict(r) for r in rows if match.candidate_week(dict(r), None) == week][:limit]
    return {
        "name": f"prod_{week.replace('-', '_')}",
        "draft": True,
        "existing_topics": [{"topic_id": t["topic_id"], "name": t["name"], "description": t["description"]}
                            for t in topics],
        "candidates": [{"signal_type": c["signal_type"], "summary": c["summary"]} for c in chosen],
        "expected": [{"index": i, "matched_topic_id": c["original_topic_id"] if c["original_topic_id"] in known else None,
                      "new_topic": None} for i, c in enumerate(chosen)],
        "notes": f"Exported from production week {week}; candidate ids {[c['candidate_id'] for c in chosen]}.",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Draft a matching scenario from one production week.")
    parser.add_argument("--week", required=True, help="ISO week, e.g. 2026-W40")
    parser.add_argument("--db-path", default="data/pi_agent.db")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--out", help="default: evals/results/matching_scenario_<week>.yaml")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    conn = db.connect(args.db_path)
    db.init_db(conn)
    try:
        scenario = build_scenario(conn, args.week, args.limit)
    finally:
        conn.close()
    if not scenario["candidates"]:
        print(f"no matched candidates captured in {args.week}", file=sys.stderr)
        return 2

    out = Path(args.out or runs.DEFAULT_RESULTS_DIR / f"matching_scenario_{args.week}.yaml")
    if out.exists() and not args.force:
        print(f"{out} already exists; pass --force to overwrite it", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(HEADER + yaml.safe_dump(scenario, sort_keys=False, allow_unicode=True, width=100), encoding="utf-8")
    print(f"wrote draft scenario with {len(scenario['candidates'])} candidates to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
