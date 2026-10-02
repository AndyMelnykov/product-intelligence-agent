"""Draft judge-calibration cases from a saved extraction run.

Usage: python evals/export_judge_cases.py evals/results/extraction-<stamp>.json [--out PATH] [--force]

Writes every summary the judge graded in that run as an unlabeled case (human_verdict: null).
Label each case, then move it into evals/golden/judge/tuning.yaml or holdout.yaml; the loader
rejects unlabeled cases. Makes no API calls.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml  # noqa: E402

from evals import harness, runs  # noqa: E402

EXTRACTION_GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "extraction")

DRAFT_HEADER = """\
# Unlabeled judge-calibration cases drafted from an extraction run.
# For each case, decide pass/fail YOURSELF from the evidence and reference summary, then set
# human_verdict. Add hand-written failing variants (invented cause, side point, too vague,
# added sentiment) so the set has real fails. Then move cases into evals/golden/judge/tuning.yaml
# or holdout.yaml.
"""


def draft_cases(record, examples):
    """Unlabeled cases for every summary the judge graded in an extraction run record, plus the
    "source/id" keys whose golden example no longer exists (so there is no evidence to copy)."""
    by_key = {(e.source, e.id): e for e in examples}
    cases, missing = [], []
    for result in record["results"]:
        summary = next((f for f in result.get("fields") or [] if f.get("field") == "summary"), None)
        if summary is None:
            continue
        example = by_key.get((result.get("source"), result.get("example_id")))
        if example is None:
            missing.append(f"{result.get('source')}/{result.get('example_id')}")
            continue
        cases.append({
            "id": f"jc-{example.id}",
            "example_id": example.id,
            "evidence": dict(example.evidence),
            "reference_summary": example.reference_summary,
            "actual_summary": str(summary.get("actual")),
            "human_verdict": None,
            "notes": None,
        })
    return cases, missing


def main(argv=None):
    parser = argparse.ArgumentParser(description="Draft unlabeled judge-calibration cases from an extraction run.")
    parser.add_argument("run_file", help="a saved extraction run, e.g. evals/results/extraction-<stamp>.json")
    parser.add_argument("--out", default=str(runs.DEFAULT_RESULTS_DIR / "judge_cases_draft.yaml"),
                        help="where to write the draft (default: evals/results/judge_cases_draft.yaml)")
    parser.add_argument("--force", action="store_true", help="overwrite --out if it already exists")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        record = runs.load_run(args.run_file)
        if record["kind"] != "extraction":
            raise runs.RunFileError(f"{args.run_file}: is a {record['kind']} run; drafts come from extraction runs")
        examples = harness.load_golden_dir(EXTRACTION_GOLDEN_DIR, "extraction")
    except (runs.RunFileError, harness.GoldenSchemaError) as e:
        print(e, file=sys.stderr)
        return 2

    cases, missing = draft_cases(record, examples)
    for key in missing:
        print(f"warning: {key} is no longer in the golden set; skipped", file=sys.stderr)
    if not cases:
        print("that run has no judged summaries to draft cases from", file=sys.stderr)
        return 2

    out = Path(args.out)
    if out.exists() and not args.force:
        print(f"{out} already exists; pass --force to overwrite it", file=sys.stderr)
        return 2
    out.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump({"cases": cases}, sort_keys=False, allow_unicode=True, width=100)
    out.write_text(DRAFT_HEADER + body, encoding="utf-8")
    print(f"wrote {len(cases)} unlabeled cases to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
