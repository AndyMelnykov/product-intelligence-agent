"""Run the extraction golden set against the real extraction prompt and model.

Usage: python evals/run_extraction_eval.py [--filter database_development] [--model MODEL]

Calls the real Anthropic API (one extraction call plus one judge call per non-skipped
example). This is a manual tool, not part of pytest or CI.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import extract  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, judge  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "extraction")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Grade extract.extract_topic against the golden set.")
    parser.add_argument("--filter", help="only run the golden file with this basename, e.g. database_development")
    parser.add_argument("--model", help=f"override extract.EXTRACTION_MODEL (default: {extract.EXTRACTION_MODEL})")
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

    try:
        client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
    except MissingCredentialError as e:
        print(e, file=sys.stderr)
        return 2

    print(f"extraction model: {extract.EXTRACTION_MODEL}, judge model: {judge.JUDGE_MODEL}")
    results = []
    for number, example in enumerate(selected, 1):
        result = harness.grade_extraction_example(example, client)
        print(f"[{number}/{len(selected)}] {example.source}/{example.id}: {result.status}")
        results.append(result)

    print()
    print(harness.format_report(harness.aggregate(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
