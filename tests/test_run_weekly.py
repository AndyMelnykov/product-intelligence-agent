import inspect
import sys
from pathlib import Path

import pytest

import publish
import run_weekly

STAGES = ("fetch", "extract", "match", "report", "materiality", "review", "publish")


def _checked(real, fake):
    """A stand-in for `real` that first proves the call would bind to the real signature (checklist §3)."""
    signature = inspect.signature(real)

    def stub(*a, **kw):
        signature.bind(*a, **kw)
        return fake(*a, **kw)

    return stub


def _stub_stages(monkeypatch, calls, *, record_kwargs=None):
    for stage in STAGES:
        module = getattr(run_weekly, stage)

        def fake(*a, _stage=stage, **kw):
            calls.append(_stage)
            if record_kwargs is not None:
                record_kwargs[_stage] = (a, kw)

        monkeypatch.setattr(module, "run", _checked(module.run, fake))


def _config(tmp_path, text, name="config.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_run_all_calls_every_stage_in_order(tmp_path, monkeypatch):
    config_path = _config(tmp_path, "subreddits:\n  - sub1\nfetch_limit_per_subreddit: 25\ntrend_window_weeks: 4\n")
    calls, seen = [], {}
    _stub_stages(monkeypatch, calls, record_kwargs=seen)

    run_weekly.run_all(config_path=config_path)

    assert calls == ["fetch", "extract", "match", "report", "materiality"]
    assert seen["fetch"][0] == (["sub1"], 25)
    assert seen["report"][1]["trend_window_weeks"] == 4
    assert seen["materiality"][1]["trend_window_weeks"] == 4


@pytest.mark.parametrize("stage", ["fetch", "extract", "match", "report", "materiality", "review"])
def test_main_dash_stage_runs_only_that_stage(tmp_path, monkeypatch, stage):
    config_path = _config(tmp_path, "subreddits:\n  - sub1\n")
    calls = []
    _stub_stages(monkeypatch, calls)

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", stage, "--config", config_path])
    run_weekly.main()

    assert calls == [stage]


def test_main_defaults_to_all_stages(tmp_path, monkeypatch):
    config_path = _config(tmp_path, "subreddits:\n  - sub1\n")
    calls = []
    _stub_stages(monkeypatch, calls)

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--config", config_path])
    run_weekly.main()

    assert calls == ["fetch", "extract", "match", "report", "materiality"]


def test_run_all_passes_the_cascade_flag_to_extract(tmp_path, monkeypatch):
    config_path = _config(tmp_path, "subreddits:\n  - sub1\nextraction_cascade: true\n")
    seen = {}
    _stub_stages(monkeypatch, [], record_kwargs=seen)

    run_weekly.run_all(config_path=config_path)

    assert seen["extract"][1]["cascade"] is True


def test_run_all_runs_review_only_when_enabled(tmp_path, monkeypatch):
    calls = []
    _stub_stages(monkeypatch, calls)

    run_weekly.run_all(config_path=_config(tmp_path, "subreddits:\n  - sub1\n", "off.yaml"))
    assert "review" not in calls

    run_weekly.run_all(config_path=_config(tmp_path, "subreddits:\n  - sub1\ntopic_review: true\n", "on.yaml"))
    assert calls[-1] == "review"


def test_run_all_skips_publish_without_teamos(tmp_path, monkeypatch, capsys):
    _stub_stages(monkeypatch, [])

    def fail(*a, **kw):
        raise AssertionError("publish.run must not be called without a teamos section")

    monkeypatch.setattr(publish, "run", fail)
    config_path = _config(tmp_path, "subreddits:\n  - sub1\n")

    run_weekly.run_all(config_path=config_path)
    publish_lines = [line for line in capsys.readouterr().out.splitlines() if "publish" in line]
    assert publish_lines == ["publish: skipped (no teamos section in config)"]

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "all", "--config", config_path])
    run_weekly.main()


def test_run_all_publishes_last_when_teamos_set(tmp_path, monkeypatch):
    calls, seen = [], {}
    _stub_stages(monkeypatch, calls, record_kwargs=seen)
    config_path = _config(tmp_path, "subreddits:\n  - sub1\nteamos:\n  repo_path: X\n")

    run_weekly.run_all(config_path=config_path)

    assert calls[-1] == "publish"
    assert seen["publish"][0] == ({"repo_path": "X", "max_signals_per_topic": 10},)


def test_stage_publish_runs_only_publish(tmp_path, monkeypatch):
    calls = []
    _stub_stages(monkeypatch, calls)
    config_path = _config(tmp_path, "subreddits:\n  - sub1\nteamos:\n  repo_path: X\n")

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "publish", "--config", config_path])
    run_weekly.main()

    assert calls == ["publish"]


def test_stage_publish_exits_nonzero_when_script_missing(tmp_path, monkeypatch):
    repo = tmp_path / "empty-teamos"
    repo.mkdir()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(f"subreddits:\n  - sub1\nteamos:\n  repo_path: {repo.as_posix()}\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "publish", "--config", str(config_path)])
    with pytest.raises(SystemExit) as exc:
        run_weekly.main()

    assert exc.value.code
    assert str(Path(repo) / "scripts" / "signals" / "signals.py") in str(exc.value.code)


def test_stage_publish_without_teamos_exits_nonzero(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "publish", "--config", str(config_path)])
    with pytest.raises(SystemExit) as exc:
        run_weekly.main()

    assert "teamos.repo_path" in str(exc.value.code)
