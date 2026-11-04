import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

import db
import weekutil

SUMMARY_MAX = 300
DETAIL_MAX = 2000
REDDIT_ORIGIN = "https://www.reddit.com"
SIGNALS_SCRIPT = Path("scripts/signals/signals.py")

_USERNAME = re.compile(r"(?<![\w/])/?u/[A-Za-z0-9_-]+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ADD_SUMMARY = re.compile(r"written: (.*); skipped duplicates: (\d+); invalid: (\d+)")


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


def run_signals_add(repo_path: str, rows: list[dict]) -> subprocess.CompletedProcess:
    """The only subprocess publish starts. Rows go in as ASCII-only JSON so the child's stdin
    code page (a console code page on Windows) cannot mangle emoji or curly quotes."""
    script = Path(repo_path) / SIGNALS_SCRIPT
    return subprocess.run(
        [sys.executable, str(script), "add", "--json", "-", "--repo-root", repo_path],
        input=json.dumps(rows), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def _parse_add_summary(stdout: str) -> tuple[int | None, int | None, int | None]:
    match = _ADD_SUMMARY.search(stdout)
    if match is None:
        return None, None, None
    written = match.group(1).strip()
    return (0 if written == "none" else len(written.split(",")), int(match.group(2)), int(match.group(3)))


def run(teamos: dict | None, db_path: str = "data/pi_agent.db", today: date | None = None) -> dict:
    if teamos is None:
        raise PublishError("teamos is not configured: set teamos.repo_path in config.yaml")
    script = Path(teamos["repo_path"]) / SIGNALS_SCRIPT
    if not script.is_file():
        raise PublishError(f"signals.py not found at {script}")

    week = weekutil.iso_week_string(today or date.today())
    conn = db.connect(db_path)
    try:
        db.init_db(conn)
        rows = collect_signals(conn, week, teamos["max_signals_per_topic"])
    finally:
        conn.close()

    result = {"week": week, "selected": len(rows), "written": 0, "skipped": 0, "invalid": 0}
    if not rows:
        print(f"publish: {week}: no material topics with publishable evidence, nothing to publish")
        return result

    proc = run_signals_add(teamos["repo_path"], rows)
    if proc.stdout.strip():
        print(proc.stdout.rstrip())
    if proc.stderr.strip():
        print(proc.stderr.rstrip(), file=sys.stderr)

    written, skipped, invalid = _parse_add_summary(proc.stdout)
    result.update(written=written, skipped=skipped, invalid=invalid)
    if written is None:
        print(f"publish: {week}: {len(rows)} selected, counts unknown (could not read signals.py output)")
    else:
        print(f"publish: {week}: {len(rows)} selected, {written} new, {skipped} already published, {invalid} invalid")
        print("publish: next: review the diff of signals.csv in TeamOSTemplate; "
              "attach new signals to a hypothesis with /hypothesis")
    if proc.returncode != 0:
        raise PublishError(f"signals.py add exited with code {proc.returncode}")
    return result
