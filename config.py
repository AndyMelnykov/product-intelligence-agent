import yaml

DEFAULT_FETCH_LIMIT = 100
DEFAULT_TREND_WINDOW_WEEKS = 8


class ConfigError(Exception):
    pass


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    subreddits = raw.get("subreddits")
    if not subreddits:
        raise ConfigError(f"config at {path} must define a non-empty 'subreddits' list")

    return {
        "subreddits": list(subreddits),
        "fetch_limit_per_subreddit": raw.get("fetch_limit_per_subreddit", DEFAULT_FETCH_LIMIT),
        "trend_window_weeks": raw.get("trend_window_weeks", DEFAULT_TREND_WINDOW_WEEKS),
        "extraction_cascade": bool(raw.get("extraction_cascade", False)),
    }
