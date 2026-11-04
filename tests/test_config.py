import pytest

from config import ConfigError, load_config


def test_load_config_applies_defaults_when_omitted(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - yourproductname\n")

    config = load_config(str(config_path))

    assert config == {
        "subreddits": ["yourproductname"],
        "fetch_limit_per_subreddit": 100,
        "trend_window_weeks": 8,
        "extraction_cascade": False,
        "topic_review": False,
        "teamos": None,
    }


def test_load_config_honors_explicit_values(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        "subreddits:\n  - a\n  - b\n"
        "fetch_limit_per_subreddit: 50\n"
        "trend_window_weeks: 4\n"
    )

    config = load_config(str(config_path))

    assert config["subreddits"] == ["a", "b"]
    assert config["fetch_limit_per_subreddit"] == 50
    assert config["trend_window_weeks"] == 4


def test_load_config_rejects_missing_subreddits(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("fetch_limit_per_subreddit: 50\n")

    with pytest.raises(ConfigError):
        load_config(str(config_path))


def test_load_config_rejects_empty_subreddits_list(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits: []\n")

    with pytest.raises(ConfigError):
        load_config(str(config_path))


def test_load_config_reads_extraction_cascade(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - a\nextraction_cascade: true\n")
    assert load_config(str(config_path))["extraction_cascade"] is True


def test_load_config_reads_topic_review(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - a\ntopic_review: true\n")
    assert load_config(str(config_path))["topic_review"] is True


def _write_config(tmp_path, extra: str) -> str:
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - a\n" + extra, encoding="utf-8")
    return str(config_path)


def test_teamos_absent_is_none(tmp_path):
    assert load_config(_write_config(tmp_path, ""))["teamos"] is None


def test_teamos_reads_path_and_cap(tmp_path):
    path = _write_config(
        tmp_path, "teamos:\n  repo_path: C:/x/TeamOS\n  max_signals_per_topic: 3\n"
    )
    assert load_config(path)["teamos"] == {
        "repo_path": "C:/x/TeamOS",
        "max_signals_per_topic": 3,
    }


def test_teamos_cap_defaults_to_10(tmp_path):
    path = _write_config(tmp_path, "teamos:\n  repo_path: C:/x/TeamOS\n")
    assert load_config(path)["teamos"]["max_signals_per_topic"] == 10


@pytest.mark.parametrize(
    "section",
    ["teamos: {}\n", "teamos:\n  max_signals_per_topic: 5\n"],
)
def test_teamos_requires_repo_path(tmp_path, section):
    with pytest.raises(ConfigError, match="repo_path"):
        load_config(_write_config(tmp_path, section))


@pytest.mark.parametrize("cap", ["0", "-1", '"ten"', "true"])
def test_teamos_rejects_bad_cap(tmp_path, cap):
    section = f"teamos:\n  repo_path: C:/x/TeamOS\n  max_signals_per_topic: {cap}\n"
    with pytest.raises(ConfigError, match="max_signals_per_topic"):
        load_config(_write_config(tmp_path, section))
