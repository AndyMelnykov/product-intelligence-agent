import yaml

DEFAULT_FETCH_LIMIT = 100
DEFAULT_TREND_WINDOW_WEEKS = 8


DEFAULT_MAX_SIGNALS_PER_TOPIC = 10


class ConfigError(Exception):
    pass


def _teamos_section(raw: dict, path: str) -> dict | None:
    section = raw.get("teamos")
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ConfigError(f"config at {path}: 'teamos' must be a mapping with a 'repo_path'")

    repo_path = section.get("repo_path")
    if not isinstance(repo_path, str) or not repo_path.strip():
        raise ConfigError(f"config at {path}: 'teamos' must define a non-empty 'repo_path'")

    cap = section.get("max_signals_per_topic", DEFAULT_MAX_SIGNALS_PER_TOPIC)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 1:
        raise ConfigError(
            f"config at {path}: 'teamos.max_signals_per_topic' must be a positive integer"
        )

    return {"repo_path": repo_path, "max_signals_per_topic": cap}


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
        "topic_review": bool(raw.get("topic_review", False)),
        "teamos": _teamos_section(raw, path),
    }
