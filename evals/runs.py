"""Save eval runs as JSON files and compare two runs to spot drift.

Run files live in evals/results/ (gitignored), one per run. Each records the models, a
fingerprint of every prompt, and a fingerprint of the golden data it graded, so a change
in scores can be traced to the model, a prompt edit, or a golden-set edit.
"""
import dataclasses
import datetime
import hashlib
import itertools
import json
import re
import subprocess
from pathlib import Path

SCHEMA_VERSION = 1
KINDS = ("extraction", "matching", "judge")
DEFAULT_RESULTS_DIR = Path(__file__).resolve().parent / "results"

_SAVE_SUFFIX_RE = re.compile(r"Z-(\d+)$")


class RunFileError(Exception):
    """A run file is missing, unreadable, or not a run record this code understands."""


def fingerprint(value):
    """First 12 hex chars of a SHA-256 over a string, or over any JSON-serializable value."""
    text = value if isinstance(value, str) else json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def golden_fingerprint(items):
    """Fingerprint of the golden examples, scenarios, or judge cases a run graded (after --filter)."""
    return fingerprint([dataclasses.asdict(item) for item in items])


def git_commit():
    """Short HEAD commit, or None when git is unavailable or this is not a checkout."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=Path(__file__).resolve().parent,
            capture_output=True, text=True, timeout=5, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def build_run_record(kind, results, report, *, models, prompts, golden, filter_name, started_at, commit):
    if kind not in KINDS:
        raise ValueError(f"unknown run kind {kind!r}")
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "started_at": started_at.astimezone(datetime.timezone.utc).isoformat(timespec="seconds"),
        "git_commit": commit,
        "filter": filter_name,
        "models": dict(models),
        "prompts": dict(prompts),
        "golden_fingerprint": golden,
        "summary": {
            "total": report.total,
            "passed": report.passed,
            "failed": report.failed,
            "errored": report.errored,
            "field_accuracy": {name: [correct, graded] for name, (correct, graded) in report.field_accuracy.items()},
            "judge_passed": report.judge_passed,
            "judge_total": report.judge_total,
        },
        "results": [dataclasses.asdict(result) for result in results],
    }


def save_run(record, results_dir=DEFAULT_RESULTS_DIR):
    """Write `record` to <results_dir>/<kind>-<UTC stamp>.json, never overwriting an earlier run."""
    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.fromisoformat(record["started_at"]).strftime("%Y%m%dT%H%M%SZ")
    text = json.dumps(record, indent=2, ensure_ascii=False, default=str) + "\n"
    for attempt in itertools.count(1):
        path = results_dir / (f"{record['kind']}-{stamp}" + ("" if attempt == 1 else f"-{attempt}") + ".json")
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(text)
        except FileExistsError:
            continue
        return path


def load_run(path):
    path = Path(path)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RunFileError(f"{path}: no such run file") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RunFileError(f"{path}: not a readable JSON file ({e})") from e
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        raise RunFileError(f"{path}: not a schema v{SCHEMA_VERSION} eval run file")
    if (record.get("kind") not in KINDS or not isinstance(record.get("summary"), dict)
            or not isinstance(record.get("results"), list)):
        raise RunFileError(f"{path}: run file is missing its kind, summary, or results")
    return record


def find_runs(results_dir, kind):
    """Saved runs of `kind` as (path, record) pairs, oldest first, plus the paths that could not be loaded."""
    found, skipped = [], []
    for path in Path(results_dir).glob(f"{kind}-*.json"):
        try:
            record = load_run(path)
        except RunFileError:
            skipped.append(path)
            continue
        if record["kind"] == kind:
            found.append((path, record))
    found.sort(key=lambda pair: (str(pair[1].get("started_at")), _save_order(pair[0])))
    return found, sorted(skipped)


def _save_order(path):
    # Runs saved within the same second are named ...Z.json, ...Z-2.json, ...Z-3.json.
    suffix = _SAVE_SUFFIX_RE.search(path.stem)
    return int(suffix.group(1)) if suffix else 1
