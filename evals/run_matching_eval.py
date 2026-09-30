"""Run the matching golden set against the real matching prompt and model.

Usage: python evals/run_matching_eval.py [--filter connection_pooling_duplicate] [--model MODEL] [--no-save]

Calls the real Anthropic API (one matcher call per scenario). Each run is saved as JSON
under evals/results/; compare runs with evals/compare_runs.py. This is a manual tool, not
part of pytest or CI.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import match  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, runs  # noqa: E402

GOLDEN_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "matching")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Grade match.call_matcher against the golden scenarios.")
    parser.add_argument("--filter", help="only run the scenario with this name, e.g. connection_pooling_duplicate")
    parser.add_argument("--model", help=f"override match.MATCH_MODEL (default: {match.MATCH_MODEL})")
    parser.add_argument("--no-save", action="store_true", help="do not save this run to --results-dir")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR),
                        help="where to save the run (default: evals/results)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        scenarios = harness.load_golden_dir(GOLDEN_DIR, "matching")
    except harness.GoldenSchemaError as e:
        print(f"golden set is malformed: {e}", file=sys.stderr)
        return 2

    selected = harness.filter_examples(scenarios, args.filter)
    if not selected:
        available = ", ".join(sorted(s.name for s in scenarios))
        print(f"no scenario named {args.filter!r}; available: {available}", file=sys.stderr)
        return 2

    if args.model:
        match.MATCH_MODEL = args.model

    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    print(f"matching model: {match.MATCH_MODEL}")
    results = []
    for number, scenario in enumerate(selected, 1):
        result = harness.grade_matching_scenario(scenario, client)
        print(f"[{number}/{len(selected)}] {scenario.name}: {result.status}")
        results.append(result)

    report = harness.aggregate(results)
    print()
    print(harness.format_report(report))

    if not args.no_save:
        record = runs.build_run_record(
            "matching", results, report,
            models={"matching": match.MATCH_MODEL},
            prompts={"matching": runs.fingerprint(match.MATCHING_PROMPT_TEMPLATE)},
            golden=runs.golden_fingerprint(selected),
            filter_name=args.filter, started_at=started_at, commit=runs.git_commit(),
        )
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
