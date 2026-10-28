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
