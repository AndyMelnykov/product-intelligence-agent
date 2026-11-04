"""Signals store: the only writer of product-development/signals/signals.csv.

Usage:
    python scripts/signals/signals.py add --json FILE|- [--repo-root .]
    python scripts/signals/signals.py check [--repo-root .]
    python scripts/signals/signals.py show SIG-### [--repo-root .]
    python scripts/signals/signals.py unassigned [--tag T] [--repo-root .]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass, fields
from datetime import date as _date
from pathlib import Path

SOURCE_TYPES = (
    "customer-call", "support", "community", "review", "competitor",
    "market", "analytics", "experiment", "survey", "prototype", "other",
)
SIGNAL_ID_IN_TEXT = re.compile(r"(?<![\w-])SIG-\d{3,}(?![\w-])")
SIGNAL_ID = re.compile(r"^SIG-\d{3,}$")
SUMMARY_MAX = 300
DETAIL_MAX = 2000

STORE = Path("product-development/signals/signals.csv")
HYPOTHESES_ROOT = Path("product-development/product/PRDs")
ACCOUNTS_ROOT = Path("product-development/product/customers/accounts")


@dataclass(frozen=True)
class Signal:
    signal_id: str
    date: str
    source_type: str
    source_ref: str
    account: str
    summary: str
    detail: str
    tags: str


@dataclass
class AddResult:
    written: list[str]
    skipped: int
    invalid: dict[int, list[str]]


COLUMNS = tuple(f.name for f in fields(Signal))


def _store(repo_root: Path) -> Path:
    return Path(repo_root) / STORE


def load(repo_root: Path) -> list[Signal]:
    path = _store(repo_root)
    if not path.exists():
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return [Signal(**{c: (r.get(c) or "") for c in COLUMNS}) for r in csv.DictReader(f)]


def validate(row: dict, repo_root: Path | None = None) -> list[str]:
    """One message per bad column; [] means valid. `signal_id` is checked only when present.

    `account` must name a folder under customers/accounts/; that is checked only when `repo_root` is given.
    """
    problems: list[str] = []

    def get(key: str) -> str:
        return (row.get(key) or "").strip()

    if row.get("signal_id") and not SIGNAL_ID.match(row["signal_id"]):
        problems.append(f"signal_id {row['signal_id']!r} must look like SIG-###")
    try:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", get("date")):
            raise ValueError
        _date.fromisoformat(get("date"))
    except ValueError:
        problems.append(f"date {row.get('date')!r} must be a real YYYY-MM-DD date")
    if get("source_type") not in SOURCE_TYPES:
        problems.append(f"source_type {row.get('source_type')!r} must be one of {', '.join(SOURCE_TYPES)}")
    if not get("source_ref"):
        problems.append("source_ref is required")
    summary = row.get("summary") or ""
    if not summary.strip():
        problems.append("summary is required")
    elif len(summary) > SUMMARY_MAX:
        problems.append(f"summary is {len(summary)} characters; max {SUMMARY_MAX}")
    detail = row.get("detail") or ""
    if len(detail) > DETAIL_MAX:
        problems.append(f"detail is {len(detail)} characters; max {DETAIL_MAX}")
    account = get("account")
    if account and repo_root is not None:
        if "/" in account or "\\" in account or not (Path(repo_root) / ACCOUNTS_ROOT / account).is_dir():
            problems.append(f"account {account!r} must be a folder under {ACCOUNTS_ROOT.as_posix()}/")
    return problems


def _next_number(existing: list[Signal]) -> int:
    numbers = [int(s.signal_id[4:]) for s in existing if SIGNAL_ID.match(s.signal_id)]
    return max(numbers, default=0) + 1


def add(repo_root: Path, rows: list[dict]) -> AddResult:
    existing = load(repo_root)
    seen = {(s.source_ref, s.summary) for s in existing}
    number = _next_number(existing)
    result = AddResult(written=[], skipped=0, invalid={})
    new: list[Signal] = []

    for index, row in enumerate(rows):
        problems = validate(row, repo_root)
        if problems:
            result.invalid[index] = problems
            continue
        key = (row["source_ref"], row["summary"])
        if key in seen:
            result.skipped += 1
            continue
        seen.add(key)
        values = {c: (row.get(c) or "") for c in COLUMNS}
        values["signal_id"] = f"SIG-{number:03d}"
        number += 1
        new.append(Signal(**values))
        result.written.append(values["signal_id"])

    if new:
        path = _store(repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not path.exists() or path.stat().st_size == 0
        with open(path, "a", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            if fresh:
                writer.writerow(COLUMNS)
            for s in new:
                writer.writerow([getattr(s, c) for c in COLUMNS])
    return result


def cited(repo_root: Path) -> dict[str, list[str]]:
    """signal_id -> repo-relative paths of hypotheses citing it."""
    root = Path(repo_root)
    out: dict[str, list[str]] = {}
    for path in sorted((root / HYPOTHESES_ROOT).rglob("*-hypothesis.md")):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        for signal_id in dict.fromkeys(SIGNAL_ID_IN_TEXT.findall(text)):
            out.setdefault(signal_id, []).append(rel)
    return out


def unassigned(repo_root: Path) -> list[Signal]:
    used = cited(repo_root)
    return [s for s in load(repo_root) if s.signal_id not in used]


def _describe(s: Signal) -> str:
    tags = f" [{s.tags}]" if s.tags else ""
    return f"{s.signal_id}  {s.date}  {s.source_type}  {s.summary}{tags}"


def _read_json(arg: str) -> list[dict]:
    text = sys.stdin.read() if arg == "-" else Path(arg).read_text(encoding="utf-8")
    data = json.loads(text)
    rows = [data] if isinstance(data, dict) else data
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError("JSON must be one object or a list of objects")
    return rows


def _cmd_add(args) -> int:
    result = add(args.repo_root, _read_json(args.json))
    for index, problems in result.invalid.items():
        for p in problems:
            print(f"error: row {index}: {p}", file=sys.stderr)
    print(f"written: {', '.join(result.written) or 'none'}; skipped duplicates: {result.skipped}; "
          f"invalid: {len(result.invalid)}")
    return 1 if result.invalid else 0


def _cmd_check(args) -> int:
    problems: list[str] = []
    seen: set[str] = set()
    for s in load(args.repo_root):
        label = s.signal_id or "(no id)"
        for p in validate(s.__dict__, args.repo_root):
            problems.append(f"{label}: {p}")
        if not s.signal_id:
            problems.append(f"{label}: signal_id is required")
        elif s.signal_id in seen:
            problems.append(f"{label}: duplicate signal_id")
        seen.add(s.signal_id)
    for p in problems:
        print(f"error: {p}", file=sys.stderr)
    if not problems:
        print(f"OK: {len(seen)} signals")
    return 1 if problems else 0


def _cmd_show(args) -> int:
    match = next((s for s in load(args.repo_root) if s.signal_id == args.signal_id), None)
    if match is None:
        print(f"error: {args.signal_id} not found", file=sys.stderr)
        return 1
    for c in COLUMNS:
        print(f"{c}: {getattr(match, c)}")
    print("cited by: " + (", ".join(cited(args.repo_root).get(match.signal_id, [])) or "no hypothesis"))
    return 0


def _cmd_unassigned(args) -> int:
    for s in unassigned(args.repo_root):
        if args.tag and args.tag not in s.tags.split(";"):
            continue
        print(_describe(s))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="signals.py", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, handler, extra in (
        ("add", _cmd_add, lambda p: p.add_argument("--json", required=True, help="FILE or - for stdin")),
        ("check", _cmd_check, lambda p: None),
        ("show", _cmd_show, lambda p: p.add_argument("signal_id")),
        ("unassigned", _cmd_unassigned, lambda p: p.add_argument("--tag")),
    ):
        p = sub.add_parser(name)
        p.add_argument("--repo-root", type=Path, default=Path("."))
        extra(p)
        p.set_defaults(handler=handler)
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
