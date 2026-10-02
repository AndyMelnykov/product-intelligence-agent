"""Measure the summary judge against human-labeled calibration cases.

Usage: python evals/run_judge_eval.py [--filter tuning] [--model MODEL] [--no-save]

Calls the real Anthropic API (one judge call per case). "Passed" here means the judge agreed
with the human label. Each run is saved as JSON under evals/results/; compare runs with
evals/compare_runs.py. This is a manual tool, not part of pytest or CI.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, judge, runs  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "judge")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Grade the summary judge against human-labeled cases.")
    parser.add_argument("--filter", help="only run the calibration file with this basename, e.g. tuning or holdout")
    parser.add_argument("--model", help=f"override judge.JUDGE_MODEL (default: {judge.JUDGE_MODEL})")
    parser.add_argument("--no-save", action="store_true", help="do not save this run to --results-dir")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR),
                        help="where to save the run (default: evals/results)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        cases = harness.load_golden_dir(GOLDEN_DIR, "judge")
    except harness.GoldenSchemaError as e:
        print(f"judge calibration set is malformed: {e}", file=sys.stderr)
        return 2

    selected = harness.filter_examples(cases, args.filter)
    if not selected:
        available = ", ".join(sorted({c.source for c in cases}))
        print(f"no calibration file named {args.filter!r}; available: {available}", file=sys.stderr)
        return 2

    if args.model:
        judge.JUDGE_MODEL = args.model

    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    print(f"judge model: {judge.JUDGE_MODEL}")
    results = []
    for number, case in enumerate(selected, 1):
        result = harness.grade_judge_case(case, client)
        print(f"[{number}/{len(selected)}] {case.source}/{case.id}: {result.status}")
        results.append(result)

    report = harness.aggregate(results)
    print()
    print(harness.format_report(report))
    print()
    print(harness.format_judge_agreement(harness.judge_agreement(results)))

    if not args.no_save:
        record = runs.build_run_record(
            "judge", results, report,
            models={"judge": judge.JUDGE_MODEL},
            prompts={"judge": runs.fingerprint(judge.JUDGE_PROMPT_TEMPLATE)},
            golden=runs.golden_fingerprint(selected),
            filter_name=args.filter, started_at=started_at, commit=runs.git_commit(),
        )
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
