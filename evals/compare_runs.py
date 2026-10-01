"""Compare two saved eval runs to see what changed.

Usage:
  python evals/compare_runs.py --kind extraction                 # latest run vs the one before it
  python evals/compare_runs.py BASELINE.json                     # a pinned baseline vs the latest other run of its kind
  python evals/compare_runs.py BASELINE.json CURRENT.json

Reads the files the eval runners save under evals/results/. Makes no API calls.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evals import runs  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser(description="Compare two saved eval runs.")
    parser.add_argument("run_files", nargs="*", metavar="RUN_FILE", help="baseline run file, then optionally the current one")
    parser.add_argument("--kind", choices=runs.KINDS, help="with no run files: compare the two latest runs of this kind")
    parser.add_argument("--results-dir", default=str(runs.DEFAULT_RESULTS_DIR),
                        help="where the runners saved results (default: evals/results)")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(errors="backslashreplace")
    if len(args.run_files) > 2:
        parser.error("give at most two run files")

    try:
        baseline_path, current_path = _pick(args)
        comparison = runs.compare_runs(runs.load_run(baseline_path), runs.load_run(current_path))
    except (runs.RunFileError, ValueError) as e:
        print(e, file=sys.stderr)
        return 2

    print(f"baseline file: {baseline_path}")
    print(f"current file:  {current_path}")
    print()
    print(runs.format_comparison(comparison))
    return 0


def _pick(args):
    if len(args.run_files) == 2:
        return Path(args.run_files[0]), Path(args.run_files[1])
    if len(args.run_files) == 1:
        baseline_path = Path(args.run_files[0])
        kind = runs.load_run(baseline_path)["kind"]
    elif args.kind:
        baseline_path, kind = None, args.kind
    else:
        raise ValueError("give one or two run files, or --kind to compare the two latest runs of that kind")

    found, skipped = runs.find_runs(args.results_dir, kind)
    for path in skipped:
        print(f"warning: skipping unreadable run file {path}", file=sys.stderr)
    paths = [path for path, _ in found if baseline_path is None or path.resolve() != baseline_path.resolve()]

    if baseline_path is not None:
        if not paths:
            raise ValueError(f"no other saved {kind} run in {args.results_dir} to compare against")
        return baseline_path, paths[-1]
    if len(paths) < 2:
        raise ValueError(f"need at least two saved {kind} runs in {args.results_dir}, found {len(paths)}")
    return paths[-2], paths[-1]


if __name__ == "__main__":
    sys.exit(main())
