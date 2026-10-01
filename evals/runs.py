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
import sys
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


def save_and_announce(record, results_dir):
    """Runner helper: save the run and say where. A failed save warns but never fails the run,
    because the report has already been printed and the API calls already paid for."""
    try:
        path = save_run(record, results_dir)
    except OSError as e:
        print(f"warning: could not save run results: {e}", file=sys.stderr)
        return None
    print(f"\nsaved run to {path}")
    return path


@dataclasses.dataclass
class Comparison:
    kind: str
    baseline_label: str
    current_label: str
    warnings: list
    pass_rate: tuple
    field_accuracy: dict
    regressions: list
    fixes: list
    status_changes: list
    only_in_baseline: list
    only_in_current: list


def compare_runs(baseline, current):
    """Diff two run records of the same kind. Warnings name every setting that differs between
    them, so a score change is not blamed on the model when the prompt or golden data moved."""
    if baseline["kind"] != current["kind"]:
        raise ValueError(f"cannot compare a {baseline['kind']} run with a {current['kind']} run")

    warnings = []
    for key, noun in (("models", "model"), ("prompts", "prompt")):
        before, after = baseline.get(key) or {}, current.get(key) or {}
        for name in sorted(before.keys() | after.keys()):
            if before.get(name) != after.get(name):
                warnings.append(f"{name} {noun} changed: {before.get(name)} -> {after.get(name)}")
    if baseline.get("golden_fingerprint") != current.get("golden_fingerprint"):
        warnings.append("golden data changed (examples or labels were added, edited, or removed)")
    if baseline.get("filter") != current.get("filter"):
        warnings.append(f"--filter differs: {baseline.get('filter')} -> {current.get('filter')}")

    before_status, after_status = _statuses(baseline), _statuses(current)
    regressions, fixes, status_changes = [], [], []
    for key in sorted(before_status.keys() & after_status.keys()):
        (was, _), (now, why) = before_status[key], after_status[key]
        if was == now:
            continue
        if (was, now) == ("passed", "failed"):
            regressions.append((key, why))
        elif (was, now) == ("failed", "passed"):
            fixes.append(key)
        else:
            status_changes.append((key, was, now))

    before_fields = baseline["summary"].get("field_accuracy") or {}
    after_fields = current["summary"].get("field_accuracy") or {}
    names = [*before_fields, *(name for name in after_fields if name not in before_fields)]

    return Comparison(
        kind=baseline["kind"],
        baseline_label=_label(baseline),
        current_label=_label(current),
        warnings=warnings,
        pass_rate=(_pass_rate(baseline["summary"]), _pass_rate(current["summary"])),
        field_accuracy={name: (before_fields.get(name), after_fields.get(name)) for name in names},
        regressions=regressions,
        fixes=fixes,
        status_changes=status_changes,
        only_in_baseline=sorted(before_status.keys() - after_status.keys()),
        only_in_current=sorted(after_status.keys() - before_status.keys()),
    )


def format_comparison(comparison):
    lines = [
        f"== Run comparison ({comparison.kind}) ==",
        f"Baseline: {comparison.baseline_label}",
        f"Current:  {comparison.current_label}",
    ]
    if comparison.warnings:
        lines += ["", "Warnings -- a score change may come from these, not only from model behavior:"]
        lines += [f"  {warning}" for warning in comparison.warnings]

    before, after = comparison.pass_rate
    lines += ["", f"Pass rate (errored excluded): {_percent(before)} -> {_percent(after)}{_delta(before, after)}"]

    if comparison.field_accuracy:
        lines += ["", "Per-field accuracy:"]
        for name, (was, now) in comparison.field_accuracy.items():
            lines.append(f"  {name:<16} {_counts(was)} -> {_counts(now)}{_delta(_ratio(was), _ratio(now))}")

    lines += ["", f"Regressions (passed -> failed): {len(comparison.regressions)}"]
    lines += [f"  {key}: {why}" for key, why in comparison.regressions]
    lines += [f"Fixes (failed -> passed): {len(comparison.fixes)}"]
    lines += [f"  {key}" for key in comparison.fixes]
    if comparison.status_changes:
        lines += ["Other status changes:"]
        lines += [f"  {key}: {was} -> {now}" for key, was, now in comparison.status_changes]
    if comparison.only_in_baseline:
        lines.append(f"Only in baseline: {', '.join(comparison.only_in_baseline)}")
    if comparison.only_in_current:
        lines.append(f"Only in current: {', '.join(comparison.only_in_current)}")
    return "\n".join(lines)


def _statuses(record):
    """{"source/example_id": (status, why it did not pass)} for every result in a run record."""
    statuses = {}
    for result in record["results"]:
        failing = [
            f"{f.get('label')}: expected {f.get('expected')!r}, got {f.get('actual')!r}"
            for f in result.get("fields") or [] if not f.get("passed")
        ]
        why = result.get("error") or "; ".join(failing)
        statuses[f"{result.get('source')}/{result.get('example_id')}"] = (result.get("status"), why)
    return statuses


def _label(record):
    return f"{record.get('started_at')} @ {record.get('git_commit') or 'unknown commit'}"


def _pass_rate(summary):
    graded = summary.get("total", 0) - summary.get("errored", 0)
    return summary.get("passed", 0) / graded if graded else None


def _ratio(counts):
    return counts[0] / counts[1] if counts and counts[1] else None


def _counts(counts):
    return f"{counts[0]}/{counts[1]} {_percent(_ratio(counts))}" if counts else "n/a"


def _percent(rate):
    return "n/a" if rate is None else f"{100 * rate:.1f}%"


def _delta(before, after):
    return "" if before is None or after is None else f"  ({100 * (after - before):+.1f} pts)"
