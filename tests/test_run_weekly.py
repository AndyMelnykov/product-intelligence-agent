import sys

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
