"""Grade the triage call against the extraction golden set's skip labels, and sweep skip thresholds."""
import triage
from evals.harness import ExampleResult, FieldResult

SWEEP_THRESHOLDS = (0.7, 0.8, 0.9, 0.95)


def grade_triage_example(example, client):
    evidence = {**example.evidence, "evidence_id": example.id}
    try:
        reply = triage.request_triage(client, evidence)
    except triage.TriageAPIError as e:
        return ExampleResult(example.id, example.source, "errored", error=str(e))
    usage = {"input_tokens": reply.input_tokens, "output_tokens": reply.output_tokens}
    try:
        decision = triage.parse_triage_reply(reply, example.id)
    except triage.TriageResponseError as e:
        return ExampleResult(example.id, example.source, "failed", error=str(e), **usage)
    expected = "skip" if example.expected["skip"] else "extract"
    passed = decision.route == expected
    return ExampleResult(example.id, example.source, "passed" if passed else "failed",
                         [FieldResult("route", "route", expected, decision.route, passed, detail=decision.reason)],
                         confidence=decision.confidence, **usage)


def triage_sweep(results, thresholds=SWEEP_THRESHOLDS):
    """For each threshold: how many posts triage would skip, how many of those the golden set also
    skips (savings) and how many it would not (false skips: real signals the extractor never sees)."""
    graded = [r for r in results if r.status != "errored" and r.fields]
    golden_skips = sum(r.fields[0].expected == "skip" for r in graded)
    rows = []
    for threshold in thresholds:
        would = [r for r in graded if r.fields[0].actual == "skip" and r.confidence is not None
                 and r.confidence >= threshold]
        false = [r.example_id for r in would if r.fields[0].expected == "extract"]
        rows.append({"threshold": threshold, "would_skip": len(would), "caught_skips": len(would) - len(false),
                     "false_skips": len(false), "false_skip_ids": false, "golden_skips": golden_skips,
                     "graded": len(graded)})
    return rows


def format_triage_sweep(rows):
    lines = ["Skip-threshold sweep (triage skips the extractor only for route=skip at or above the threshold):",
             "  threshold  skipped  caught/golden skips  false skips  share of posts skipped"]
    for row in rows:
        share = f"{100 * row['would_skip'] / row['graded']:.1f}%" if row["graded"] else "n/a"
        line = (f"  {row['threshold']:<9.2f}  {row['would_skip']:<7}  "
                f"{row['caught_skips']}/{row['golden_skips']:<17}  {row['false_skips']:<11}  {share}")
        if row["false_skip_ids"]:
            line += f"  false skips: {', '.join(row['false_skip_ids'])}"
        lines.append(line)
    return "\n".join(lines)
