import argparse
import sys

import extract
import fetch
import match
import materiality
import publish
import report
import review
from config import load_config

STAGES = ["fetch", "extract", "match", "report", "materiality", "review", "publish"]


def run_all(config_path="config.yaml", today=None):
    config = load_config(config_path)
    fetch.run(config["subreddits"], config["fetch_limit_per_subreddit"], today=today)
    extract.run(today=today, cascade=config["extraction_cascade"])
    match.run(today=today)
    report.run(trend_window_weeks=config["trend_window_weeks"], today=today)
    materiality.run(trend_window_weeks=config["trend_window_weeks"], today=today)
    if config["topic_review"]:
        review.run(today=today)
    if config["teamos"]:
        publish.run(config["teamos"], today=today)
    else:
        print("publish: skipped (no teamos section in config)")


def main():
    parser = argparse.ArgumentParser(description="Reddit signal pipeline")
    parser.add_argument("--stage", choices=STAGES + ["all"], default="all")
    parser.add_argument("--config", default="config.yaml")
    args = parser.parse_args()

    try:
        _dispatch(args)
    except publish.PublishError as e:
        sys.exit(f"publish: {e}")


def _dispatch(args):
    if args.stage == "all":
        run_all(args.config)
        return

    config = load_config(args.config)
    if args.stage == "fetch":
        fetch.run(config["subreddits"], config["fetch_limit_per_subreddit"])
    elif args.stage == "extract":
        extract.run(cascade=config["extraction_cascade"])
    elif args.stage == "match":
        match.run()
    elif args.stage == "report":
        report.run(trend_window_weeks=config["trend_window_weeks"])
    elif args.stage == "materiality":
        materiality.run(trend_window_weeks=config["trend_window_weeks"])
    elif args.stage == "review":
        review.run()
    elif args.stage == "publish":
        publish.run(config["teamos"])


if __name__ == "__main__":
    main()
