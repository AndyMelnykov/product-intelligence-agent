import sys
from pathlib import Path

import pytest

import publish
import run_weekly


def test_run_all_calls_every_stage_in_order(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\nfetch_limit_per_subreddit: 25\ntrend_window_weeks: 4\n")

    calls = []
    monkeypatch.setattr(run_weekly.fetch, "run", lambda *a, **kw: calls.append(("fetch", a, kw)))
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: calls.append(("extract", a, kw)))
    monkeypatch.setattr(run_weekly.match, "run", lambda *a, **kw: calls.append(("match", a, kw)))
    monkeypatch.setattr(run_weekly.report, "run", lambda *a, **kw: calls.append(("report", a, kw)))
    monkeypatch.setattr(run_weekly.materiality, "run", lambda *a, **kw: calls.append(("materiality", a, kw)))

    run_weekly.run_all(config_path=str(config_path))

    assert [c[0] for c in calls] == ["fetch", "extract", "match", "report", "materiality"]
    fetch_call = calls[0]
    assert fetch_call[1] == (["sub1"], 25)
    report_call = calls[3]
    assert report_call[2]["trend_window_weeks"] == 4
    materiality_call = calls[4]
    assert materiality_call[2]["trend_window_weeks"] == 4


def test_main_dash_stage_fetch_runs_only_fetch(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\n")

    calls = []
    monkeypatch.setattr(run_weekly.fetch, "run", lambda *a, **kw: calls.append("fetch"))
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: calls.append("extract"))
    monkeypatch.setattr(run_weekly.match, "run", lambda *a, **kw: calls.append("match"))
    monkeypatch.setattr(run_weekly.report, "run", lambda *a, **kw: calls.append("report"))
    monkeypatch.setattr(run_weekly.materiality, "run", lambda *a, **kw: calls.append("materiality"))

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "fetch", "--config", str(config_path)])
    run_weekly.main()

    assert calls == ["fetch"]


def test_main_dash_stage_materiality_runs_only_materiality(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\n")

    calls = []
    monkeypatch.setattr(run_weekly.fetch, "run", lambda *a, **kw: calls.append("fetch"))
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: calls.append("extract"))
    monkeypatch.setattr(run_weekly.match, "run", lambda *a, **kw: calls.append("match"))
    monkeypatch.setattr(run_weekly.report, "run", lambda *a, **kw: calls.append("report"))
    monkeypatch.setattr(run_weekly.materiality, "run", lambda *a, **kw: calls.append("materiality"))

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "materiality", "--config", str(config_path)])
    run_weekly.main()

    assert calls == ["materiality"]


def test_main_defaults_to_all_stages(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\n")

    calls = []
    monkeypatch.setattr(run_weekly.fetch, "run", lambda *a, **kw: calls.append("fetch"))
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: calls.append("extract"))
    monkeypatch.setattr(run_weekly.match, "run", lambda *a, **kw: calls.append("match"))
    monkeypatch.setattr(run_weekly.report, "run", lambda *a, **kw: calls.append("report"))
    monkeypatch.setattr(run_weekly.materiality, "run", lambda *a, **kw: calls.append("materiality"))

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--config", str(config_path)])
    run_weekly.main()

    assert calls == ["fetch", "extract", "match", "report", "materiality"]


def test_run_all_passes_the_cascade_flag_to_extract(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\nextraction_cascade: true\n")
    seen = {}
    for stage in ("fetch", "match", "report", "materiality"):
        monkeypatch.setattr(getattr(run_weekly, stage), "run", lambda *a, **kw: None)
    monkeypatch.setattr(run_weekly.extract, "run", lambda *a, **kw: seen.update(kw))

    run_weekly.run_all(config_path=str(config_path))

    assert seen["cascade"] is True


def test_run_all_runs_review_only_when_enabled(tmp_path, monkeypatch):
    calls = []
    for stage in ("fetch", "extract", "match", "report", "materiality", "review"):
        monkeypatch.setattr(getattr(run_weekly, stage), "run", lambda *a, _s=stage, **kw: calls.append(_s))

    off = tmp_path / "off.yaml"
    off.write_text("subreddits:\n  - sub1\n")
    run_weekly.run_all(config_path=str(off))
    assert "review" not in calls

    on = tmp_path / "on.yaml"
    on.write_text("subreddits:\n  - sub1\ntopic_review: true\n")
    run_weekly.run_all(config_path=str(on))
    assert calls[-1] == "review"


def _stub_stages(monkeypatch, calls):
    for stage in ("fetch", "extract", "match", "report", "materiality", "review"):
        monkeypatch.setattr(getattr(run_weekly, stage), "run", lambda *a, _s=stage, **kw: calls.append(_s))


def test_run_all_skips_publish_without_teamos(tmp_path, monkeypatch, capsys):
    calls = []
    _stub_stages(monkeypatch, calls)

    def fail(*a, **kw):
        raise AssertionError("publish.run must not be called without a teamos section")

    monkeypatch.setattr(publish, "run", fail)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\n", encoding="utf-8")

    run_weekly.run_all(config_path=str(config_path))
    publish_lines = [line for line in capsys.readouterr().out.splitlines() if "publish" in line]
    assert publish_lines == ["publish: skipped (no teamos section in config)"]

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "all", "--config", str(config_path)])
    run_weekly.main()


def test_run_all_publishes_last_when_teamos_set(tmp_path, monkeypatch):
    calls = []
    _stub_stages(monkeypatch, calls)
    seen = {}

    def fake_publish(teamos, **kw):
        calls.append("publish")
        seen["teamos"] = teamos

    monkeypatch.setattr(publish, "run", fake_publish)
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\nteamos:\n  repo_path: X\n", encoding="utf-8")

    run_weekly.run_all(config_path=str(config_path))

    assert calls[-1] == "publish"
    assert seen["teamos"] == {"repo_path": "X", "max_signals_per_topic": 10}


def test_stage_publish_runs_only_publish(tmp_path, monkeypatch):
    calls = []
    _stub_stages(monkeypatch, calls)
    monkeypatch.setattr(publish, "run", lambda *a, **kw: calls.append("publish"))
    config_path = tmp_path / "config.yaml"
    config_path.write_text("subreddits:\n  - sub1\nteamos:\n  repo_path: X\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["run_weekly.py", "--stage", "publish", "--config", str(config_path)])
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
