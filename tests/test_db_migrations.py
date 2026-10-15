import sqlite3

import pytest

import db
from fingerprints import fingerprint


def legacy_connection(path):
    """A database exactly as the pre-migration code created it: SCHEMA_SQL only, user_version 0."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(db.SCHEMA_SQL)
    conn.commit()
    return conn


def test_fingerprint_is_stable_for_strings_and_json_values():
    assert fingerprint("abc") == fingerprint("abc")
    assert len(fingerprint("abc")) == 12
    assert fingerprint({"b": 1, "a": 2}) == fingerprint({"a": 2, "b": 1})


def test_runs_module_still_exposes_fingerprint():
    from evals import runs
    assert runs.fingerprint("abc") == fingerprint("abc")


def test_init_db_brings_a_fresh_database_to_the_latest_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    latest = max((version for version, _ in db.MIGRATIONS), default=0)
    assert db.schema_version(conn) == latest


def test_apply_migrations_runs_each_pending_migration_once_in_order():
    conn = db.connect(":memory:")
    db.init_db(conn)
    calls = []
    base = db.schema_version(conn)
    migrations = [
        (base + 1, lambda c: calls.append(base + 1)),
        (base + 2, lambda c: calls.append(base + 2)),
    ]

    db.apply_migrations(conn, migrations)
    db.apply_migrations(conn, migrations)

    assert calls == [base + 1, base + 2]
    assert db.schema_version(conn) == base + 2


def test_failed_migration_rolls_back_its_changes_and_version():
    conn = db.connect(":memory:")
    db.init_db(conn)
    base = db.schema_version(conn)

    def broken(c):
        c.execute("CREATE TABLE half_done (x INTEGER)")
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        db.apply_migrations(conn, [(base + 1, broken)])

    assert db.schema_version(conn) == base
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "half_done" not in tables


def test_init_db_upgrades_a_legacy_database_and_keeps_its_rows(tmp_path):
    path = tmp_path / "legacy.db"
    conn = legacy_connection(path)
    conn.execute(
        "INSERT INTO evidence (evidence_id, source_type, source_name, source_url, captured_at, published_at, "
        "title, content, metadata) VALUES ('EV-2026-000001', 'reddit_post', 'sub', '/a', "
        "'2026-08-15T00:00:00+00:00', '2026-08-14T00:00:00+00:00', 't', 'c', '{\"reddit_post_id\": \"a1\"}')"
    )
    conn.commit()
    conn.close()

    conn = db.connect(str(path))
    db.init_db(conn)

    assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 1
    assert db.schema_version(conn) == max((v for v, _ in db.MIGRATIONS), default=0)


def test_add_column_is_idempotent():
    conn = db.connect(":memory:")
    db.init_db(conn)
    db._add_column(conn, "evidence", "scratch TEXT")
    db._add_column(conn, "evidence", "scratch TEXT")
    assert "scratch" in db._column_names(conn, "evidence")


def test_utc_now_is_iso_utc_to_the_second():
    stamp = db.utc_now()
    assert stamp.endswith("+00:00")
    assert len(stamp) == len("2026-10-14T12:00:00+00:00")
