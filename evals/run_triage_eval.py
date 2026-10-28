"""Run the triage call over the extraction golden set and sweep skip thresholds.

Usage: python evals/run_triage_eval.py [--filter FILE] [--model MODEL] [--no-save]

One Haiku call per example. The golden "skip" label is the expected route. The sweep shows,
per threshold, the share of posts triage would skip and every false skip (a real signal the
extractor would never see). Saved as kind "triage". Not part of CI.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import triage  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, runs, triage_eval  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "extraction")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Grade triage against the extraction golden set's skip labels.")
    parser.add_argument("--filter", help="only run the golden file with this basename, e.g. database_development")
    parser.add_argument("--model", help=f"override triage.TRIAGE_MODEL (default: {triage.TRIAGE_MODEL})")
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
        triage.TRIAGE_MODEL = args.model
    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    print(f"triage model: {triage.TRIAGE_MODEL} (effort {triage.TRIAGE_EFFORT})")
    results = []
    for number, example in enumerate(selected, 1):
        result = triage_eval.grade_triage_example(example, client)
        print(f"[{number}/{len(selected)}] {example.source}/{example.id}: {result.status}")
        results.append(result)

    report = harness.aggregate(results)
    sweep = triage_eval.triage_sweep(results)
    print()
    print(harness.format_report(report))
    print()
    print(triage_eval.format_triage_sweep(sweep))

    if not args.no_save:
        record = runs.build_run_record(
            "triage", results, report, models={"triage": triage.TRIAGE_MODEL},
            prompts={"triage": runs.fingerprint(triage.TRIAGE_PROMPT_TEMPLATE)},
            golden=runs.golden_fingerprint(selected), filter_name=args.filter, started_at=started_at,
            commit=runs.git_commit(), settings={"triage_effort": triage.TRIAGE_EFFORT},
        )
        record["triage_sweep"] = sweep
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
