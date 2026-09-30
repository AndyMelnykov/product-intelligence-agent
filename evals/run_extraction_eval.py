"""Run the extraction golden set against the real extraction prompt and model.

Usage: python evals/run_extraction_eval.py [--filter database_development] [--model MODEL] [--no-save]

Calls the real Anthropic API (one extraction call plus one judge call per non-skipped
example). Each run is saved as JSON under evals/results/; compare runs with
evals/compare_runs.py. This is a manual tool, not part of pytest or CI.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import extract  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, judge, runs  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "extraction")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Grade extract.extract_topic against the golden set.")
    parser.add_argument("--filter", help="only run the golden file with this basename, e.g. database_development")
    parser.add_argument("--model", help=f"override extract.EXTRACTION_MODEL (default: {extract.EXTRACTION_MODEL})")
    parser.add_argument("--no-save", action="store_true", help="do not save this run to --results-dir")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR),
                        help="where to save the run (default: evals/results)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        examples = harness.load_golden_dir(GOLDEN_DIR, "extraction")
    except harness.GoldenSchemaError as e:
        print(f"golden set is malformed: {e}", file=sys.stderr)
        return 2

    selected = harness.filter_examples(examples, args.filter)
    if not selected:
        available = ", ".join(sorted({e.source for e in examples}))
        print(f"no golden file named {args.filter!r}; available: {available}", file=sys.stderr)
        return 2

    if args.model:
        extract.EXTRACTION_MODEL = args.model

    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    print(f"extraction model: {extract.EXTRACTION_MODEL}, judge model: {judge.JUDGE_MODEL}")
    results = []
    for number, example in enumerate(selected, 1):
        result = harness.grade_extraction_example(example, client)
        print(f"[{number}/{len(selected)}] {example.source}/{example.id}: {result.status}")
        results.append(result)

    report = harness.aggregate(results)
    print()
    print(harness.format_report(report))

    if not args.no_save:
        record = runs.build_run_record(
            "extraction", results, report,
            models={"extraction": extract.EXTRACTION_MODEL, "judge": judge.JUDGE_MODEL},
            prompts={
                "extraction": runs.fingerprint(extract.EXTRACTION_PROMPT_TEMPLATE),
                "judge": runs.fingerprint(judge.JUDGE_PROMPT_TEMPLATE),
            },
            golden=runs.golden_fingerprint(selected),
            filter_name=args.filter, started_at=started_at, commit=runs.git_commit(),
        )
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
