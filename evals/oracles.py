"""Label-free checks ("oracles") for the matching and extraction stages.

Golden sets need a human label for every example. These checks need none: they ask the model the
same question in ways that must not change the answer, and count how often the answer changes.

Matching, per scenario (4 calls):
- identity: the same input twice. This is the model's own run-to-run noise; read the other two
  checks against it. If identity fails often, order and self_match failures say little.
- order: the candidates shuffled. Where a candidate lands must not depend on its position.
- self_match: every topic the first run proposed is added to the registry and the same
  candidates are sent again. Each must now match the topic it created.

Extraction, per example (1 + N calls): N cached paraphrases of the post must get the same skip
decision and signal_type as the original did (see evals/paraphrase.py).
"""
from dataclasses import dataclass, field

import extract
import match
from evals.harness import ExampleResult, FieldResult


@dataclass(frozen=True)
class Placement:
    """Where each candidate landed: an existing topic id, "NEW", or "INVALID", plus which
    candidates share a new topic. Equality ignores new-topic names and slugs: two runs that group
    the same candidates under differently worded new topics agree."""
    assignments: tuple
    new_groups: frozenset
    slugs: tuple = field(default=(), compare=False)
    new_topics: dict = field(default_factory=dict, compare=False, hash=False)


def placement(candidates, decisions, existing_topics):
    resolutions = match.resolve_decisions(candidates, decisions, existing_topics)
    assignments, slugs, groups, topics = [], [], {}, {}
    for r in resolutions:
        if r.action == "create":
            slug = r.new_topic["slug"]
            assignments.append("NEW")
            slugs.append(slug)
            groups.setdefault(slug, set()).add(r.index)
            topics.setdefault(slug, r.new_topic)
        else:
            assignments.append(r.topic_id if r.action == "match" else "INVALID")
            slugs.append(None)
    return Placement(tuple(assignments), frozenset(frozenset(g) for g in groups.values()), tuple(slugs), topics)


def unshuffle(p, order):
    """`p` was computed on candidates reordered so that position j held original candidate order[j]."""
    assignments, slugs = [None] * len(order), [None] * len(order)
    for j, original in enumerate(order):
        assignments[original] = p.assignments[j]
        slugs[original] = p.slugs[j] if p.slugs else None
    groups = frozenset(frozenset(order[j] for j in group) for group in p.new_groups)
    return Placement(tuple(assignments), groups, tuple(slugs), p.new_topics)


def describe(p):
    return f"{list(p.assignments)} new groups {sorted(sorted(g) for g in p.new_groups)}"


def _place(client, candidates, existing):
    return placement(candidates, match.call_matcher(client, candidates, existing), existing)


def _compare(check, expected, actual):
    return FieldResult(check, check, describe(expected), describe(actual), expected == actual)


def _status(fields):
    return "passed" if all(f.passed for f in fields) else "failed"


def check_matching_scenario(scenario, client, rng):
    candidates, existing = scenario.candidates, scenario.existing_topics
    try:
        base = _place(client, candidates, existing)
        fields = [_compare("identity", base, _place(client, candidates, existing))]

        order = list(range(len(candidates)))
        rng.shuffle(order)
        if order != sorted(order):
            shuffled = [candidates[i] for i in order]
            fields.append(_compare("order", base, unshuffle(_place(client, shuffled, existing), order)))

        if base.new_topics:
            oracle_ids = {slug: f"TOPIC-ORACLE-{k}" for k, slug in enumerate(sorted(base.new_topics), 1)}
            registry = [*existing, *({"topic_id": oracle_ids[slug], "slug": slug, "name": topic["name"],
                                      "description": topic["description"]} for slug, topic in base.new_topics.items())]
            expected = tuple(oracle_ids[base.slugs[i]] if a == "NEW" else a for i, a in enumerate(base.assignments))
            after = _place(client, candidates, registry)
            fields.append(FieldResult("self_match", "self_match", str(list(expected)), str(list(after.assignments)),
                                      after.assignments == expected))
    except match.MatchAPIError as e:
        return ExampleResult(scenario.name, scenario.source, "errored", error=str(e))
    except match.MatchResponseError as e:
        return ExampleResult(scenario.name, scenario.source, "failed", error=str(e))
    return ExampleResult(scenario.name, scenario.source, _status(fields), fields)


def _label(client, evidence):
    result = extract.extract_topic(client, evidence)
    return "SKIP" if result is None else result["signal_type"]


def check_extraction_stability(example, paraphrases, client):
    try:
        original = _label(client, {**example.evidence, "evidence_id": example.id})
    except extract.ExtractionAPIError as e:
        return ExampleResult(example.id, example.source, "errored", error=str(e))
    except extract.ExtractionResponseError as e:
        return ExampleResult(example.id, example.source, "failed", error=f"original: {e}")

    fields = []
    for k, item in enumerate(paraphrases):
        label = f"paraphrase[{k}]"
        try:
            got = _label(client, {**item, "evidence_id": f"{example.id}~{k}"})
        except extract.ExtractionAPIError as e:
            return ExampleResult(example.id, example.source, "errored", fields, error=str(e))
        except extract.ExtractionResponseError as e:
            fields.append(FieldResult("stability", label, original, f"unusable: {e}", False))
            continue
        fields.append(FieldResult("stability", label, original, got, got == original))
    return ExampleResult(example.id, example.source, _status(fields), fields)
