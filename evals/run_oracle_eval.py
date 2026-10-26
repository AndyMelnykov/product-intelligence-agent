"""Run the label-free oracle checks against the real API.

Usage:
  python evals/run_oracle_eval.py --kind matching [--filter SCENARIO] [--seed 0]
  python evals/run_oracle_eval.py --kind extraction [--filter FILE] [--paraphrases 3] [--generate-missing]

Matching: 4 calls per scenario (identity, order, self-match). Extraction: 1 + N calls per example
over cached paraphrases; --generate-missing first makes one paraphrase call per example that has
none cached and writes them to evals/golden/paraphrases/ (skim them before committing). Each run
is saved under evals/results/ as kind oracle_matching or oracle_extraction. Not part of CI.
"""
import argparse
import datetime
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import anthropic  # noqa: E402

import extract  # noqa: E402
import match  # noqa: E402
from credentials import MissingCredentialError, get_secret  # noqa: E402
from evals import harness, oracles, paraphrase, runs  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden")


def main(argv=None, client=None):
    parser = argparse.ArgumentParser(description="Label-free oracle checks for matching and extraction.")
    parser.add_argument("--kind", choices=("matching", "extraction"), required=True)
    parser.add_argument("--filter", help="matching: scenario name; extraction: golden file basename")
    parser.add_argument("--seed", type=int, default=0, help="matching: shuffle seed")
    parser.add_argument("--paraphrases", type=int, default=3, help="extraction: paraphrases per example")
    parser.add_argument("--generate-missing", action="store_true",
                        help="extraction: generate and cache paraphrases for examples that have none (costs API calls)")
    parser.add_argument("--paraphrase-dir", default=str(paraphrase.PARAPHRASE_DIR))
    parser.add_argument("--no-save", action="store_true", help="do not save this run to --results-dir")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR),
                        help="where to save the run (default: evals/results)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")

    try:
        items = harness.load_golden_dir(os.path.join(GOLDEN, args.kind), args.kind)
    except harness.GoldenSchemaError as e:
        print(f"golden set is malformed: {e}", file=sys.stderr)
        return 2
    selected = harness.filter_examples(items, args.filter)
    if not selected:
        print(f"nothing in the {args.kind} golden set matches --filter {args.filter!r}", file=sys.stderr)
        return 2

    cached, missing = {}, []
    if args.kind == "extraction":
        try:
            for source in sorted({e.source for e in selected}):
                cached[source] = paraphrase.load_paraphrases(source, args.paraphrase_dir)
        except harness.GoldenSchemaError as e:
            print(f"paraphrase cache is malformed: {e}", file=sys.stderr)
            return 2
        missing = [e for e in selected if not cached[e.source].get(e.id)]
        if missing and not args.generate_missing:
            if len(missing) == len(selected):
                print(f"no cached paraphrases in {args.paraphrase_dir}; rerun with --generate-missing", file=sys.stderr)
                return 2
            print(f"warning: {len(missing)} example(s) have no cached paraphrases and are skipped "
                  f"(--generate-missing would create them)", file=sys.stderr)
            selected = [e for e in selected if e not in missing]
            missing = []

    if client is None:
        try:
            client = anthropic.Anthropic(api_key=get_secret("anthropic_api_key"))
        except MissingCredentialError as e:
            print(e, file=sys.stderr)
            return 2

    started_at = datetime.datetime.now(datetime.timezone.utc)
    results = []
    if args.kind == "matching":
        models = {"matching": match.MATCH_MODEL}
        prompts = {"matching": runs.fingerprint(match.MATCHING_PROMPT_TEMPLATE)}
        golden = runs.golden_fingerprint(selected)
        for number, scenario in enumerate(selected, 1):
            result = oracles.check_matching_scenario(scenario, client, random.Random(args.seed + number))
            print(f"[{number}/{len(selected)}] {scenario.name}: {result.status}")
            results.append(result)
    else:
        for example in missing:
            try:
                cached[example.source][example.id] = paraphrase.generate_paraphrases(
                    client, example.evidence, args.paraphrases)
            except paraphrase.ParaphraseError as e:
                print(f"warning: could not paraphrase {example.source}/{example.id}: {e}", file=sys.stderr)
        for source in sorted({e.source for e in missing}):
            print(f"wrote paraphrases to {paraphrase.save_paraphrases(source, cached[source], args.paraphrase_dir)}")

        models = {"extraction": extract.EXTRACTION_MODEL, "paraphrase": paraphrase.PARAPHRASE_MODEL}
        prompts = {"extraction": runs.fingerprint(extract.EXTRACTION_PROMPT_TEMPLATE),
                   "paraphrase": runs.fingerprint(paraphrase.PARAPHRASE_PROMPT_TEMPLATE)}
        used = {}
        runnable = [e for e in selected if cached[e.source].get(e.id)]
        for number, example in enumerate(runnable, 1):
            example_paraphrases = cached[example.source][example.id][:args.paraphrases]
            used[f"{example.source}/{example.id}"] = example_paraphrases
            result = oracles.check_extraction_stability(example, example_paraphrases, client)
            print(f"[{number}/{len(runnable)}] {example.source}/{example.id}: {result.status}")
            results.append(result)
        golden = runs.fingerprint([runs.golden_fingerprint(runnable), used])

    report = harness.aggregate(results)
    print()
    print(harness.format_report(report))

    if not args.no_save:
        record = runs.build_run_record(
            f"oracle_{args.kind}", results, report, models=models, prompts=prompts, golden=golden,
            filter_name=args.filter, started_at=started_at, commit=runs.git_commit(),
        )
        runs.save_and_announce(record, args.results_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
