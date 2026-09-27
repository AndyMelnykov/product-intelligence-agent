"""Golden-set loading, grading, and aggregation for the extraction/matching evals.

Everything here is deterministic Python. The API-calling code lives in extract.py,
match.py and evals/judge.py.
"""
import datetime
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

import extract
import match
from evals import judge

ENTITY_KEYS = {"type", "company", "product"}
EXTRACTION_EXPECTED_KEYS = {"skip", "signal_type", "entity", "effective_date"}


class GoldenSchemaError(Exception):
    """A golden file is malformed -- a bug in the eval data, not a model result."""


@dataclass
class ExtractionExample:
    id: str
    source: str
    evidence: dict
    expected: dict
    reference_summary: str | None
    notes: str | None = None


@dataclass
class MatchingScenario:
    name: str
    source: str
    existing_topics: list
    candidates: list
    expected: list
    notes: str | None = None


@dataclass
class FieldResult:
    field: str
    label: str
    expected: object
    actual: object
    passed: bool
    detail: str | None = None


@dataclass
class ExampleResult:
    example_id: str
    source: str
    status: str
    fields: list = field(default_factory=list)
    error: str | None = None


@dataclass
class Report:
    total: int
    passed: int
    failed: int
    errored: int
    field_accuracy: dict
    signal_type_confusion: Counter
    judge_passed: int
    judge_total: int
    failures: list
    errors: list


def load_golden_dir(path, kind):
    """Load and validate every .yaml/.yml file in `path`. `kind` is "extraction" or "matching"."""
    loaders = {"extraction": _load_extraction_file, "matching": _load_matching_file}
    if kind not in loaders:
        raise ValueError(f"unknown golden kind {kind!r}")

    files = sorted(p for p in Path(path).iterdir() if p.suffix in (".yaml", ".yml"))
    if not files:
        raise GoldenSchemaError(f"{path}: no .yaml golden files found")

    items, seen = [], {}
    for file_path in files:
        for item in loaders[kind](file_path, _read_yaml(file_path)):
            key = item.id if kind == "extraction" else item.name
            if key in seen:
                what = "id" if kind == "extraction" else "scenario name"
                _fail(file_path, key, f"duplicate {what}, also defined in {seen[key]}")
            seen[key] = file_path.name
            items.append(item)
    return items


def filter_examples(items, name):
    """Keep extraction examples whose golden file stem is `name`, or the matching scenario named `name`."""
    if name is None:
        return list(items)
    return [item for item in items if (item.source if isinstance(item, ExtractionExample) else item.name) == name]


def _read_yaml(file_path):
    try:
        data = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise GoldenSchemaError(f"{file_path.name}: invalid YAML: {e}") from e
    if not isinstance(data, dict):
        raise GoldenSchemaError(f"{file_path.name}: top level must be a mapping")
    return data


def _fail(file_path, item_id, message):
    where = f"{file_path.name} [{item_id}]" if item_id else file_path.name
    raise GoldenSchemaError(f"{where}: {message}")


def _require_str(file_path, item_id, mapping, key, allow_empty=False):
    value = mapping.get(key)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(file_path, item_id, f"'{key}' must be a {'' if allow_empty else 'non-empty '}string")
    return value


def _normalize_date(file_path, item_id, value):
    if value is None:
        return None
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value.isoformat()
    if isinstance(value, str):
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            pass
        else:
            return value
    _fail(file_path, item_id, f"'expected.effective_date' must be null or a YYYY-MM-DD date, got {value!r}")


def _parse_entity(file_path, item_id, value):
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        _fail(file_path, item_id, "'expected.entity' must be null or a mapping with any of type/company/product")
    unknown = value.keys() - ENTITY_KEYS
    if unknown:
        _fail(file_path, item_id, f"unknown keys in 'expected.entity': {sorted(unknown)}")
    for key, entity_value in value.items():
        if entity_value is not None and not isinstance(entity_value, str):
            _fail(file_path, item_id, f"'expected.entity.{key}' must be a string or null")
    return dict(value)


def _load_extraction_file(file_path, data):
    _require_str(file_path, None, data, "product_area")
    examples = data.get("examples")
    if not isinstance(examples, list) or not examples:
        _fail(file_path, None, "'examples' must be a non-empty list")
    return [_parse_extraction_example(file_path, position, raw) for position, raw in enumerate(examples)]


def _parse_extraction_example(file_path, position, raw):
    if not isinstance(raw, dict):
        _fail(file_path, f"#{position}", "example must be a mapping")
    example_id = raw.get("id")
    if not isinstance(example_id, str) or not example_id.strip():
        _fail(file_path, f"#{position}", "'id' must be a non-empty string")

    evidence = raw.get("evidence")
    if not isinstance(evidence, dict):
        _fail(file_path, example_id, "'evidence' must be a mapping")
    evidence = {
        "title": _require_str(file_path, example_id, evidence, "title"),
        "content": _require_str(file_path, example_id, evidence, "content", allow_empty=True),
    }

    expected = raw.get("expected")
    if not isinstance(expected, dict):
        _fail(file_path, example_id, "'expected' must be a mapping")
    unknown = expected.keys() - EXTRACTION_EXPECTED_KEYS
    if unknown:
        _fail(file_path, example_id, f"unknown keys in 'expected': {sorted(unknown)}")
    skip = expected.get("skip")
    if not isinstance(skip, bool):
        _fail(file_path, example_id, "'expected.skip' must be true or false")

    source = file_path.stem
    notes = raw.get("notes")
    if skip:
        return ExtractionExample(example_id, source, evidence, {"skip": True}, None, notes)

    for key in ("signal_type", "entity", "effective_date"):
        if key not in expected:
            _fail(file_path, example_id, f"'expected.{key}' is required when skip is false (use null for none)")
    if expected["signal_type"] not in extract.SIGNAL_TYPES:
        _fail(file_path, example_id, f"invalid expected signal_type {expected['signal_type']!r}")

    return ExtractionExample(
        id=example_id,
        source=source,
        evidence=evidence,
        expected={
            "skip": False,
            "signal_type": expected["signal_type"],
            "entity": _parse_entity(file_path, example_id, expected["entity"]),
            "effective_date": _normalize_date(file_path, example_id, expected["effective_date"]),
        },
        reference_summary=_require_str(file_path, example_id, raw, "reference_summary").strip(),
        notes=notes,
    )


def _load_matching_file(file_path, data):
    name = _require_str(file_path, None, data, "name")

    topics = data.get("existing_topics")
    if not isinstance(topics, list):
        _fail(file_path, name, "'existing_topics' must be a list (may be empty)")
    topic_ids = set()
    for topic in topics:
        if not isinstance(topic, dict):
            _fail(file_path, name, "each existing topic must be a mapping")
        topic_id = _require_str(file_path, name, topic, "topic_id")
        _require_str(file_path, name, topic, "name")
        _require_str(file_path, name, topic, "description")
        if topic_id in topic_ids:
            _fail(file_path, name, f"duplicate topic_id {topic_id!r}")
        topic_ids.add(topic_id)

    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        _fail(file_path, name, "'candidates' must be a non-empty list")
    for candidate in candidates:
        if not isinstance(candidate, dict):
            _fail(file_path, name, "each candidate must be a mapping")
        if candidate.get("signal_type") not in extract.SIGNAL_TYPES:
            _fail(file_path, name, f"invalid candidate signal_type {candidate.get('signal_type')!r}")
        _require_str(file_path, name, candidate, "summary")

    expected = data.get("expected")
    if not isinstance(expected, list):
        _fail(file_path, name, "'expected' must be a list")
    by_index = {}
    for decision in expected:
        if not isinstance(decision, dict):
            _fail(file_path, name, "each expected decision must be a mapping")
        index = decision.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(candidates):
            _fail(file_path, name, f"expected index {index!r} is not a valid candidate index")
        if index in by_index:
            _fail(file_path, name, f"duplicate expected index {index}")
        if "matched_topic_id" not in decision:
            _fail(file_path, name, f"expected[{index}] needs 'matched_topic_id' (null for a new topic)")
        topic_id = decision["matched_topic_id"]
        new_topic = decision.get("new_topic")
        if topic_id is not None:
            if topic_id not in topic_ids:
                _fail(file_path, name, f"expected[{index}] matched_topic_id {topic_id!r} is not in existing_topics")
            if new_topic is not None:
                _fail(file_path, name, f"expected[{index}] cannot set both matched_topic_id and new_topic")
        elif new_topic is not None and not isinstance(new_topic, dict):
            _fail(file_path, name, f"expected[{index}] new_topic must be null or a mapping")
        by_index[index] = {"index": index, "matched_topic_id": topic_id}

    missing = set(range(len(candidates))) - by_index.keys()
    if missing:
        _fail(file_path, name, f"no expected decision for candidate indexes {sorted(missing)}")

    return [MatchingScenario(
        name=name,
        source=file_path.stem,
        existing_topics=topics,
        candidates=candidates,
        expected=[by_index[i] for i in range(len(candidates))],
        notes=data.get("notes"),
    )]


def grade_extraction_example(example, client):
    try:
        actual = extract.extract_topic(client, {**example.evidence, "evidence_id": example.id})
    except extract.ExtractionAPIError as e:
        return ExampleResult(example.id, example.source, "errored", error=str(e))
    except extract.ExtractionResponseError as e:
        return ExampleResult(example.id, example.source, "failed", error=str(e))

    expected = example.expected
    actual_skip = actual is None
    fields = [_exact("skip", expected["skip"], actual_skip)]
    if expected["skip"] or actual_skip:
        return _finish(example.id, example.source, fields)

    fields.append(_exact("signal_type", expected["signal_type"], actual["signal_type"]))
    fields.append(FieldResult(
        "entity", "entity", expected["entity"], actual["entity"],
        entity_matches(expected["entity"], actual["entity"]),
    ))
    fields.append(_exact("effective_date", expected["effective_date"], actual["effective_date"]))

    try:
        verdict = judge.judge_summary(example.evidence, example.reference_summary, actual["summary"], client)
    except judge.JudgeError as e:
        return ExampleResult(example.id, example.source, "errored", fields, error=f"judge: {e}")
    fields.append(FieldResult(
        "summary", "summary", example.reference_summary, actual["summary"], verdict.passed, detail=verdict.reason,
    ))
    return _finish(example.id, example.source, fields)


def entity_matches(expected, actual):
    """Null must match null. Otherwise each key the golden file names must match, ignoring case
    and surrounding whitespace. Keys the golden file leaves out (usually the free-form 'type')
    are not graded. An entity whose values are all null counts as null."""
    if actual is not None and all(value is None for value in actual.values()):
        actual = None
    if expected is None or actual is None:
        return expected is None and actual is None
    return all(_normalize_text(actual.get(key)) == _normalize_text(value) for key, value in expected.items())


def _normalize_text(value):
    return value.strip().casefold() if isinstance(value, str) else value


def _exact(name, expected, actual):
    return FieldResult(name, name, expected, actual, expected == actual)


def _finish(example_id, source, fields):
    status = "passed" if all(f.passed for f in fields) else "failed"
    return ExampleResult(example_id, source, status, fields)


def grade_matching_scenario(scenario, client):
    try:
        decisions = match.call_matcher(client, scenario.candidates, scenario.existing_topics)
    except match.MatchAPIError as e:
        return ExampleResult(scenario.name, scenario.source, "errored", error=str(e))
    except match.MatchResponseError as e:
        return ExampleResult(scenario.name, scenario.source, "failed", error=str(e))

    if not isinstance(decisions, list) or not all(isinstance(d, dict) for d in decisions):
        return ExampleResult(
            scenario.name, scenario.source, "failed",
            error=f"matcher returned {type(decisions).__name__}, expected a JSON array of objects: {decisions!r}",
        )

    decisions_by_index = {}
    for decision in decisions:
        index = decision.get("index")
        # Non-int indexes (including unhashable ones) match no candidate, so they surface as missing decisions.
        if isinstance(index, int) and not isinstance(index, bool):
            decisions_by_index.setdefault(index, []).append(decision)

    fields = []
    for expected in scenario.expected:
        index = expected["index"]
        want = expected["matched_topic_id"]
        field_name = "match_existing" if want else "new_topic"
        label = f"candidate[{index}]"
        expected_desc = want or "NEW"
        got = decisions_by_index.get(index, [])
        if len(got) != 1:
            fields.append(FieldResult(field_name, label, expected_desc, f"{len(got)} decisions for this index", False))
            continue
        actual_desc, passed = _grade_decision(want, got[0])
        fields.append(FieldResult(field_name, label, expected_desc, actual_desc, passed))
    return _finish(scenario.name, scenario.source, fields)


def _grade_decision(want, decision):
    """Returns (description of what the model decided, whether it matches `want`)."""
    got_id = decision.get("matched_topic_id")
    new_topic = decision.get("new_topic")
    if got_id:
        if new_topic:
            return f"{got_id} (new_topic also set)", False
        return got_id, got_id == want
    if _is_complete_new_topic(new_topic):
        return f"NEW: {new_topic['name']}", want is None
    return f"invalid decision {decision!r}", False


def _is_complete_new_topic(new_topic):
    # match.apply_matches needs all three keys to create the topic.
    return isinstance(new_topic, dict) and all(
        isinstance(new_topic.get(key), str) and new_topic[key].strip() for key in ("name", "slug", "description")
    )


def aggregate(results):
    field_accuracy = {}
    confusion = Counter()
    judge_passed = judge_total = 0
    for result in results:
        if result.status == "errored":
            continue
        for f in result.fields:
            correct, graded = field_accuracy.get(f.field, (0, 0))
            field_accuracy[f.field] = (correct + int(f.passed), graded + 1)
            if f.field == "signal_type" and not f.passed:
                confusion[(f.expected, f.actual)] += 1
            if f.field == "summary":
                judge_total += 1
                judge_passed += int(f.passed)

    return Report(
        total=len(results),
        passed=sum(r.status == "passed" for r in results),
        failed=sum(r.status == "failed" for r in results),
        errored=sum(r.status == "errored" for r in results),
        field_accuracy=field_accuracy,
        signal_type_confusion=confusion,
        judge_passed=judge_passed,
        judge_total=judge_total,
        failures=[r for r in results if r.status == "failed"],
        errors=[r for r in results if r.status == "errored"],
    )


def format_report(report):
    lines = [
        "== Eval report ==",
        f"Examples: {report.total} total, {report.passed} passed, {report.failed} failed, {report.errored} errored",
    ]

    if report.field_accuracy:
        lines += ["", "Per-field accuracy (errored examples excluded):"]
        for name, (correct, graded) in report.field_accuracy.items():
            lines.append(f"  {name:<16} {correct}/{graded}  {_percent(correct, graded)}")

    if report.judge_total:
        lines += ["", f"Summary judge pass rate: {report.judge_passed}/{report.judge_total} "
                      f"({_percent(report.judge_passed, report.judge_total)})"]

    if report.signal_type_confusion:
        lines += ["", "signal_type confusion (expected -> actual):"]
        for (expected, actual), count in report.signal_type_confusion.most_common():
            lines.append(f"  {expected} -> {actual}  x{count}")

    if report.failures:
        lines += ["", "Failures:"]
        for result in report.failures:
            lines.append(f"  [{result.source}] {result.example_id}")
            if result.error:
                lines.append(f"    error: {result.error}")
            for f in result.fields:
                if not f.passed:
                    line = f"    {f.label}: expected {f.expected!r}, got {f.actual!r}"
                    if f.detail:
                        line += f" -- judge: {f.detail}"
                    lines.append(line)

    if report.errors:
        lines += ["", "Errored (API failures, not counted as model mistakes):"]
        for result in report.errors:
            lines.append(f"  [{result.source}] {result.example_id}: {result.error}")

    return "\n".join(lines)


def _percent(numerator, denominator):
    return f"{100 * numerator / denominator:.1f}%" if denominator else "n/a"
