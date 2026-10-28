import json
import sqlite3
from collections import Counter
from datetime import date, datetime, timezone

from weekutil import iso_week_string

DEFAULT_DB_PATH = "data/pi_agent.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY,
  source_type TEXT NOT NULL,
  source_name TEXT NOT NULL,
  source_url TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  published_at TEXT NOT NULL,
  title TEXT,
  content TEXT,
  metadata TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_topic (
  topic_id TEXT PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  aliases TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_candidate (
  candidate_id TEXT PRIMARY KEY,
  evidence_id TEXT NOT NULL REFERENCES evidence(evidence_id),
  signal_type TEXT NOT NULL,
  topic_id TEXT REFERENCES canonical_topic(topic_id),
  summary TEXT NOT NULL,
  confidence REAL NOT NULL,
  entity TEXT,
  effective_date TEXT
);

CREATE TABLE IF NOT EXISTS topic_weekly_mentions (
  topic_id TEXT NOT NULL REFERENCES canonical_topic(topic_id),
  period TEXT NOT NULL,
  mentions INTEGER NOT NULL,
  PRIMARY KEY (topic_id, period)
);

CREATE TABLE IF NOT EXISTS material_signal (
  signal_id TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  signal_type TEXT NOT NULL,
  topic_id TEXT REFERENCES canonical_topic(topic_id),
  entity TEXT NOT NULL,
  summary TEXT NOT NULL,
  confidence_label TEXT NOT NULL,
  materiality_label TEXT NOT NULL,
  materiality_score REAL NOT NULL,
  materiality_reasons TEXT NOT NULL,
  evidence_ids TEXT NOT NULL,
  change_type TEXT NOT NULL,
  recommended_next_step TEXT NOT NULL
);
"""


class DBError(Exception):
    pass


def connect(path=DEFAULT_DB_PATH):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def _migration_1_llm_call(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS llm_call (
          call_id INTEGER PRIMARY KEY AUTOINCREMENT,
          stage TEXT NOT NULL,
          subject_key TEXT NOT NULL,
          model TEXT NOT NULL,
          prompt_fingerprint TEXT NOT NULL,
          request_meta TEXT,
          status TEXT NOT NULL,
          raw_response TEXT,
          parsed TEXT,
          error TEXT,
          stop_reason TEXT,
          input_tokens INTEGER,
          output_tokens INTEGER,
          started_at TEXT NOT NULL,
          finished_at TEXT,
          applied_at TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_call_stage_status ON llm_call(stage, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_llm_call_subject ON llm_call(stage, subject_key)")


def _migration_2_evidence_identity(conn):
    _add_column(conn, "evidence", "source_key TEXT")
    _add_column(conn, "evidence", "duplicate_of TEXT REFERENCES evidence(evidence_id)")

    # Backfill keys for rows fetched before keys existed. The first row per Reddit post keeps the
    # key; later copies of the same post are marked duplicate_of the first and keep no key.
    first_by_key = {}
    for row in conn.execute("SELECT evidence_id, source_type, metadata FROM evidence ORDER BY evidence_id").fetchall():
        post_id = json.loads(row["metadata"]).get("reddit_post_id")
        if row["source_type"] != "reddit_post" or not post_id:
            continue
        key = f"reddit_post:{post_id}"
        if key in first_by_key:
            conn.execute("UPDATE evidence SET duplicate_of = ? WHERE evidence_id = ?", (first_by_key[key], row["evidence_id"]))
        else:
            first_by_key[key] = row["evidence_id"]
            conn.execute("UPDATE evidence SET source_key = ? WHERE evidence_id = ?", (key, row["evidence_id"]))

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_source_key ON evidence(source_key) WHERE source_key IS NOT NULL"
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS fetch_cursor (
          source_type TEXT NOT NULL,
          source_name TEXT NOT NULL,
          cursor TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (source_type, source_name)
        )"""
    )


def _migration_3_extraction_outcome(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS evidence_extraction (
          evidence_id TEXT PRIMARY KEY REFERENCES evidence(evidence_id),
          status TEXT NOT NULL,
          attempts INTEGER NOT NULL,
          decided_by TEXT,
          last_call_id INTEGER REFERENCES llm_call(call_id),
          updated_at TEXT NOT NULL
        )"""
    )
    _add_column(conn, "signal_candidate", "llm_call_id INTEGER REFERENCES llm_call(call_id)")
    conn.execute(
        "INSERT OR IGNORE INTO evidence_extraction (evidence_id, status, attempts, decided_by, last_call_id, updated_at) "
        "SELECT DISTINCT evidence_id, 'candidate', 0, 'backfill', NULL, ? FROM signal_candidate",
        (utc_now(),),
    )


def _migration_4_candidate_matching(conn):
    _add_column(conn, "signal_candidate", "match_call_id INTEGER REFERENCES llm_call(call_id)")
    _add_column(conn, "signal_candidate", "match_attempts INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "signal_candidate", "match_error TEXT")


def _migration_5_signal_week_and_outbox(conn):
    _add_column(conn, "material_signal", "week TEXT")
    for row in conn.execute("SELECT signal_id, created_at FROM material_signal WHERE week IS NULL").fetchall():
        week = iso_week_string(datetime.fromisoformat(row["created_at"]).date())
        conn.execute("UPDATE material_signal SET week = ? WHERE signal_id = ?", (week, row["signal_id"]))
    conn.execute("CREATE INDEX IF NOT EXISTS idx_material_signal_topic_week ON material_signal(topic_id, week)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS event_outbox (
          outbox_id INTEGER PRIMARY KEY AUTOINCREMENT,
          signal_id TEXT NOT NULL UNIQUE REFERENCES material_signal(signal_id),
          envelope TEXT NOT NULL,
          created_at TEXT NOT NULL,
          emitted_at TEXT
        )"""
    )
    # Signals created before the outbox existed were already appended to events.jsonl.
    conn.execute(
        "INSERT OR IGNORE INTO event_outbox (signal_id, envelope, created_at, emitted_at) "
        "SELECT signal_id, '{}', created_at, created_at FROM material_signal"
    )


def _migration_6_topic_versions(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS topic_version (
          topic_id TEXT NOT NULL REFERENCES canonical_topic(topic_id),
          version INTEGER NOT NULL,
          name TEXT NOT NULL,
          description TEXT,
          aliases TEXT NOT NULL,
          valid_from_week TEXT NOT NULL,
          reason TEXT NOT NULL,
          recorded_at TEXT NOT NULL,
          PRIMARY KEY (topic_id, version)
        )"""
    )
    _add_column(conn, "canonical_topic", "status TEXT NOT NULL DEFAULT 'active'")
    _add_column(conn, "canonical_topic", "merged_into TEXT REFERENCES canonical_topic(topic_id)")
    _add_column(conn, "signal_candidate", "original_topic_id TEXT REFERENCES canonical_topic(topic_id)")
    conn.execute(
        "UPDATE signal_candidate SET original_topic_id = topic_id WHERE topic_id IS NOT NULL AND original_topic_id IS NULL"
    )
    conn.execute(
        "INSERT OR IGNORE INTO topic_version (topic_id, version, name, description, aliases, valid_from_week, reason, "
        "recorded_at) SELECT topic_id, 1, name, description, aliases, first_seen, 'backfill', ? FROM canonical_topic",
        (utc_now(),),
    )


def _migration_7_topic_lineage(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS topic_lineage (
          lineage_id INTEGER PRIMARY KEY AUTOINCREMENT,
          kind TEXT NOT NULL,
          from_topic_id TEXT NOT NULL REFERENCES canonical_topic(topic_id),
          to_topic_id TEXT NOT NULL REFERENCES canonical_topic(topic_id),
          week TEXT NOT NULL,
          reason TEXT NOT NULL,
          decided_by TEXT NOT NULL,
          candidate_ids TEXT NOT NULL,
          created_at TEXT NOT NULL
        )"""
    )


# Schema changes after the v0 baseline above. Each entry is (version, function); the function
# runs inside one transaction together with the PRAGMA user_version bump. Never edit SCHEMA_SQL
# or renumber an entry that has shipped: append a new migration instead.
MIGRATIONS = [
    (1, _migration_1_llm_call),
    (2, _migration_2_evidence_identity),
    (3, _migration_3_extraction_outcome),
    (4, _migration_4_candidate_matching),
    (5, _migration_5_signal_week_and_outbox),
    (6, _migration_6_topic_versions),
    (7, _migration_7_topic_lineage),
]


def init_db(conn):
    conn.executescript(SCHEMA_SQL)
    apply_migrations(conn)


def schema_version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def apply_migrations(conn, migrations=None):
    migrations = MIGRATIONS if migrations is None else migrations
    current = schema_version(conn)
    for version, migrate in migrations:
        if version <= current:
            continue
        conn.execute("BEGIN")
        try:
            migrate(conn)
            conn.execute(f"PRAGMA user_version = {int(version)}")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        current = version


def _column_names(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table, column_sql):
    name = column_sql.split()[0]
    if name not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_sql}")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _next_sequence_id(conn, table, id_column, prefix, width):
    cursor = conn.execute(
        f"SELECT {id_column} FROM {table} WHERE {id_column} LIKE ?", (f"{prefix}%",)
    )
    max_seq = 0
    for (existing_id,) in cursor.fetchall():
        max_seq = max(max_seq, int(existing_id[len(prefix):]))
    return f"{prefix}{max_seq + 1:0{width}d}"


def _evidence_row_to_dict(row):
    d = dict(row)
    d["metadata"] = json.loads(d["metadata"])
    return d


def _topic_row_to_dict(row):
    d = dict(row)
    d["aliases"] = json.loads(d["aliases"])
    return d


def _signal_candidate_row_to_dict(row):
    d = dict(row)
    d["entity"] = json.loads(d["entity"]) if d["entity"] is not None else None
    return d


def insert_evidence(conn, *, source_type, source_name, source_url, captured_at,
                     published_at, title, content, metadata, source_key=None):
    year = captured_at[:4]
    evidence_id = _next_sequence_id(conn, "evidence", "evidence_id", f"EV-{year}-", 6)
    conn.execute(
        "INSERT INTO evidence (evidence_id, source_type, source_name, source_url, "
        "captured_at, published_at, title, content, metadata, source_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (evidence_id, source_type, source_name, source_url, captured_at, published_at,
         title, content, json.dumps(metadata, sort_keys=True), source_key),
    )
    return evidence_id


def insert_evidence_if_new(conn, *, source_key, **fields):
    """Insert unless evidence with this source_key already exists. Returns the new id, or None."""
    if conn.execute("SELECT 1 FROM evidence WHERE source_key = ?", (source_key,)).fetchone():
        return None
    return insert_evidence(conn, source_key=source_key, **fields)


def get_fetch_cursor(conn, source_type, source_name):
    row = conn.execute(
        "SELECT cursor FROM fetch_cursor WHERE source_type = ? AND source_name = ?", (source_type, source_name)
    ).fetchone()
    return row["cursor"] if row else None


def set_fetch_cursor(conn, source_type, source_name, cursor):
    conn.execute(
        "INSERT INTO fetch_cursor (source_type, source_name, cursor, updated_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(source_type, source_name) DO UPDATE SET cursor = excluded.cursor, updated_at = excluded.updated_at",
        (source_type, source_name, cursor, utc_now()),
    )


def get_evidence(conn, evidence_id):
    row = conn.execute("SELECT * FROM evidence WHERE evidence_id = ?", (evidence_id,)).fetchone()
    return _evidence_row_to_dict(row) if row else None


def get_evidence_without_candidate(conn):
    rows = conn.execute(
        "SELECT e.* FROM evidence e LEFT JOIN signal_candidate sc ON sc.evidence_id = e.evidence_id "
        "WHERE sc.candidate_id IS NULL ORDER BY e.evidence_id"
    ).fetchall()
    return [_evidence_row_to_dict(r) for r in rows]


def get_evidence_for_topic(conn, topic_id):
    rows = conn.execute(
        "SELECT e.* FROM evidence e JOIN signal_candidate sc ON sc.evidence_id = e.evidence_id "
        "WHERE sc.topic_id = ? ORDER BY e.evidence_id",
        (topic_id,),
    ).fetchall()
    return [_evidence_row_to_dict(r) for r in rows]


def insert_signal_candidate(conn, *, evidence_id, signal_type, summary, confidence, topic_id=None,
                             entity=None, effective_date=None, llm_call_id=None):
    evidence = get_evidence(conn, evidence_id)
    if evidence is None:
        raise DBError(f"cannot create signal_candidate: evidence {evidence_id} does not exist")
    year = evidence["captured_at"][:4]
    candidate_id = _next_sequence_id(conn, "signal_candidate", "candidate_id", f"SC-{year}-", 5)
    conn.execute(
        "INSERT INTO signal_candidate (candidate_id, evidence_id, signal_type, topic_id, summary, "
        "confidence, entity, effective_date, llm_call_id, original_topic_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (candidate_id, evidence_id, signal_type, topic_id, summary, confidence,
         json.dumps(entity, sort_keys=True) if entity is not None else None, effective_date, llm_call_id, topic_id),
    )
    return candidate_id


def get_evidence_pending_extraction(conn):
    """Evidence that is not a duplicate and has either no extraction outcome yet or a retryable one."""
    rows = conn.execute(
        "SELECT e.* FROM evidence e LEFT JOIN evidence_extraction x ON x.evidence_id = e.evidence_id "
        "WHERE e.duplicate_of IS NULL AND (x.evidence_id IS NULL OR x.status = 'retryable') "
        "ORDER BY e.evidence_id"
    ).fetchall()
    return [_evidence_row_to_dict(r) for r in rows]


def get_extraction_state(conn, evidence_id):
    row = conn.execute("SELECT * FROM evidence_extraction WHERE evidence_id = ?", (evidence_id,)).fetchone()
    return dict(row) if row else None


def record_extraction_outcome(conn, evidence_id, status, call_id, *, decided_by):
    if status not in ("candidate", "skipped"):
        raise ValueError(f"outcome status must be 'candidate' or 'skipped', got {status!r}")
    conn.execute(
        "INSERT INTO evidence_extraction (evidence_id, status, attempts, decided_by, last_call_id, updated_at) "
        "VALUES (?, ?, 0, ?, ?, ?) ON CONFLICT(evidence_id) DO UPDATE SET status = excluded.status, "
        "decided_by = excluded.decided_by, last_call_id = excluded.last_call_id, updated_at = excluded.updated_at",
        (evidence_id, status, decided_by, call_id, utc_now()),
    )


def record_extraction_failure(conn, evidence_id, call_id, *, count_attempt, max_attempts):
    """Record a failed attempt. Returns the new status: 'retryable', or 'dead' once max_attempts counted failures are reached."""
    row = conn.execute("SELECT attempts FROM evidence_extraction WHERE evidence_id = ?", (evidence_id,)).fetchone()
    attempts = (row["attempts"] if row else 0) + (1 if count_attempt else 0)
    status = "dead" if attempts >= max_attempts else "retryable"
    conn.execute(
        "INSERT INTO evidence_extraction (evidence_id, status, attempts, decided_by, last_call_id, updated_at) "
        "VALUES (?, ?, ?, NULL, ?, ?) ON CONFLICT(evidence_id) DO UPDATE SET status = excluded.status, "
        "attempts = excluded.attempts, last_call_id = excluded.last_call_id, updated_at = excluded.updated_at",
        (evidence_id, status, attempts, call_id, utc_now()),
    )
    return status


def set_candidate_topic(conn, candidate_id, topic_id, match_call_id=None):
    conn.execute(
        "UPDATE signal_candidate SET topic_id = ?, original_topic_id = COALESCE(original_topic_id, ?), "
        "match_call_id = COALESCE(?, match_call_id), match_error = NULL WHERE candidate_id = ?",
        (topic_id, topic_id, match_call_id, candidate_id),
    )


def record_match_failure(conn, candidate_id, reason):
    conn.execute(
        "UPDATE signal_candidate SET match_attempts = match_attempts + 1, match_error = ? WHERE candidate_id = ?",
        (reason, candidate_id),
    )


_CANDIDATE_WITH_WEEK = "SELECT sc.*, e.captured_at FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id"


def get_candidates_without_topic(conn, max_attempts=None):
    sql, params = _CANDIDATE_WITH_WEEK + " WHERE sc.topic_id IS NULL", ()
    if max_attempts is not None:
        sql, params = sql + " AND sc.match_attempts < ?", (max_attempts,)
    rows = conn.execute(sql + " ORDER BY sc.candidate_id", params).fetchall()
    return [_signal_candidate_row_to_dict(r) for r in rows]


def get_candidates_by_ids(conn, candidate_ids):
    if not candidate_ids:
        return []
    rows = {r["candidate_id"]: r for r in conn.execute(
        _CANDIDATE_WITH_WEEK + f" WHERE sc.candidate_id IN ({','.join('?' * len(candidate_ids))})", tuple(candidate_ids),
    )}
    return [_signal_candidate_row_to_dict(rows[i]) for i in candidate_ids if i in rows]


def get_canonical_topics(conn, include_merged=False):
    where = "" if include_merged else " WHERE status = 'active'"
    rows = conn.execute(f"SELECT * FROM canonical_topic{where} ORDER BY topic_id").fetchall()
    return [_topic_row_to_dict(r) for r in rows]


def get_canonical_topic_by_slug(conn, slug):
    row = conn.execute("SELECT * FROM canonical_topic WHERE slug = ?", (slug,)).fetchone()
    return _topic_row_to_dict(row) if row else None


def insert_canonical_topic(conn, *, slug, name, description, aliases, first_seen, last_seen, valid_from_week=None):
    topic_id = _next_sequence_id(conn, "canonical_topic", "topic_id", "TOPIC-", 4)
    try:
        conn.execute(
            "INSERT INTO canonical_topic (topic_id, slug, name, description, aliases, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (topic_id, slug, name, description, json.dumps(aliases), first_seen, last_seen),
        )
    except sqlite3.IntegrityError as e:
        raise DBError(f"cannot create canonical_topic with slug {slug!r}: {e}") from e
    _insert_topic_version(conn, topic_id, 1, name, description, aliases, valid_from_week or first_seen, "created")
    return topic_id


def _insert_topic_version(conn, topic_id, version, name, description, aliases, valid_from_week, reason):
    conn.execute(
        "INSERT INTO topic_version (topic_id, version, name, description, aliases, valid_from_week, reason, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (topic_id, version, name, description, json.dumps(aliases), valid_from_week, reason, utc_now()),
    )


def update_topic_definition(conn, topic_id, *, week, reason, name=None, description=None, aliases=None):
    current = conn.execute("SELECT * FROM canonical_topic WHERE topic_id = ?", (topic_id,)).fetchone()
    if current is None:
        raise DBError(f"no topic {topic_id}")
    new_name = name if name is not None else current["name"]
    new_description = description if description is not None else current["description"]
    new_aliases = aliases if aliases is not None else json.loads(current["aliases"])
    conn.execute(
        "UPDATE canonical_topic SET name = ?, description = ?, aliases = ? WHERE topic_id = ?",
        (new_name, new_description, json.dumps(new_aliases), topic_id),
    )
    version = conn.execute("SELECT MAX(version) FROM topic_version WHERE topic_id = ?", (topic_id,)).fetchone()[0] + 1
    _insert_topic_version(conn, topic_id, version, new_name, new_description, new_aliases, week, reason)
    return version


def _topic_version_row_to_dict(row):
    d = dict(row)
    d["aliases"] = json.loads(d["aliases"])
    return d


def get_topic_versions(conn, topic_id):
    rows = conn.execute("SELECT * FROM topic_version WHERE topic_id = ? ORDER BY version", (topic_id,)).fetchall()
    return [_topic_version_row_to_dict(r) for r in rows]


def get_topic_version_as_of(conn, topic_id, week):
    row = conn.execute(
        "SELECT * FROM topic_version WHERE topic_id = ? AND valid_from_week <= ? ORDER BY version DESC LIMIT 1",
        (topic_id, week),
    ).fetchone()
    return _topic_version_row_to_dict(row) if row else None


def update_topic_last_seen(conn, topic_id, last_seen):
    conn.execute("UPDATE canonical_topic SET last_seen = ? WHERE topic_id = ?", (last_seen, topic_id))


def increment_topic_weekly_mentions(conn, topic_id, period, amount=1):
    conn.execute(
        "INSERT INTO topic_weekly_mentions (topic_id, period, mentions) VALUES (?, ?, ?) "
        "ON CONFLICT(topic_id, period) DO UPDATE SET mentions = mentions + excluded.mentions",
        (topic_id, period, amount),
    )


def get_topic_weekly_mentions(conn, topic_id):
    rows = conn.execute(
        "SELECT period, mentions FROM topic_weekly_mentions WHERE topic_id = ?", (topic_id,)
    ).fetchall()
    return {row["period"]: row["mentions"] for row in rows}


def _week_of_timestamp(iso_timestamp):
    return iso_week_string(date.fromisoformat(iso_timestamp[:10]))


def derived_topic_weekly_mentions(conn, topic_id):
    rows = conn.execute(
        "SELECT e.captured_at FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id "
        "WHERE sc.topic_id = ? AND e.duplicate_of IS NULL",
        (topic_id,),
    ).fetchall()
    return dict(Counter(_week_of_timestamp(r["captured_at"]) for r in rows))


def refresh_topic_weekly_mentions(conn, topic_ids):
    """Rebuild the cached weekly counts of these topics from their non-duplicate candidates."""
    for topic_id in sorted(set(topic_ids)):
        conn.execute("DELETE FROM topic_weekly_mentions WHERE topic_id = ?", (topic_id,))
        for period, mentions in derived_topic_weekly_mentions(conn, topic_id).items():
            conn.execute(
                "INSERT INTO topic_weekly_mentions (topic_id, period, mentions) VALUES (?, ?, ?)",
                (topic_id, period, mentions),
            )


def rebuild_all_topic_weekly_mentions(conn):
    """Refresh every topic's cache. Returns {topic_id: (before, after)} for topics whose counts changed."""
    diffs = {}
    for topic in get_canonical_topics(conn, include_merged=True):
        before = get_topic_weekly_mentions(conn, topic["topic_id"])
        after = derived_topic_weekly_mentions(conn, topic["topic_id"])
        if before != after:
            diffs[topic["topic_id"]] = (before, after)
            refresh_topic_weekly_mentions(conn, [topic["topic_id"]])
    return diffs


def extend_topic_seen(conn, topic_id, week):
    conn.execute(
        "UPDATE canonical_topic SET first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?) WHERE topic_id = ?",
        (week, week, topic_id),
    )


def resolve_topic_id(conn, topic_id):
    """Follow merged_into links to the active topic a (possibly merged) topic now lives in."""
    seen = set()
    while topic_id not in seen:
        seen.add(topic_id)
        row = conn.execute("SELECT status, merged_into FROM canonical_topic WHERE topic_id = ?", (topic_id,)).fetchone()
        if row is None or row["status"] != "merged" or not row["merged_into"]:
            return topic_id
        topic_id = row["merged_into"]
    raise DBError(f"merge cycle involving {topic_id}")


def search_evidence_and_candidates(conn, keyword):
    like = f"%{keyword}%"
    rows = conn.execute(
        "SELECT sc.candidate_id, sc.summary, sc.signal_type, e.evidence_id, e.title, e.source_url "
        "FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id "
        "WHERE e.title LIKE ? OR e.content LIKE ? OR sc.summary LIKE ?",
        (like, like, like),
    ).fetchall()
    return [dict(r) for r in rows]


def _material_signal_row_to_dict(row):
    d = dict(row)
    d["entity"] = json.loads(d["entity"])
    d["materiality_reasons"] = json.loads(d["materiality_reasons"])
    d["evidence_ids"] = json.loads(d["evidence_ids"])
    return d


def insert_material_signal(conn, *, created_at, signal_type, topic_id, entity, summary, confidence_label,
                            materiality_label, materiality_score, materiality_reasons, evidence_ids,
                            change_type, recommended_next_step, week=None):
    year = created_at[:4]
    signal_id = _next_sequence_id(conn, "material_signal", "signal_id", f"SIG-{year}-", 4)
    conn.execute(
        "INSERT INTO material_signal (signal_id, created_at, signal_type, topic_id, entity, summary, "
        "confidence_label, materiality_label, materiality_score, materiality_reasons, evidence_ids, "
        "change_type, recommended_next_step, week) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (signal_id, created_at, signal_type, topic_id, json.dumps(entity, sort_keys=True), summary,
         confidence_label, materiality_label, materiality_score, json.dumps(materiality_reasons),
         json.dumps(evidence_ids), change_type, recommended_next_step, week),
    )
    return signal_id


def get_latest_material_signal_for_topic_week(conn, topic_id, week):
    row = conn.execute(
        "SELECT * FROM material_signal WHERE topic_id = ? AND week = ? ORDER BY signal_id DESC LIMIT 1",
        (topic_id, week),
    ).fetchone()
    return _material_signal_row_to_dict(row) if row else None


def enqueue_event(conn, signal_id, envelope):
    conn.execute(
        "INSERT INTO event_outbox (signal_id, envelope, created_at) VALUES (?, ?, ?)",
        (signal_id, json.dumps(envelope), utc_now()),
    )


def get_pending_outbox(conn):
    rows = conn.execute("SELECT * FROM event_outbox WHERE emitted_at IS NULL ORDER BY outbox_id").fetchall()
    return [{**dict(r), "envelope": json.loads(r["envelope"])} for r in rows]


def mark_outbox_emitted(conn, outbox_id):
    conn.execute("UPDATE event_outbox SET emitted_at = ? WHERE outbox_id = ?", (utc_now(), outbox_id))


def get_material_signal(conn, signal_id):
    row = conn.execute("SELECT * FROM material_signal WHERE signal_id = ?", (signal_id,)).fetchone()
    return _material_signal_row_to_dict(row) if row else None


def get_candidates_for_topic(conn, topic_id):
    rows = conn.execute(
        "SELECT sc.candidate_id, sc.evidence_id, sc.signal_type, sc.summary, sc.confidence, e.captured_at "
        "FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id "
        "WHERE sc.topic_id = ? AND e.duplicate_of IS NULL ORDER BY sc.candidate_id",
        (topic_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def pipeline_health(conn, *, max_match_attempts):
    def count(sql, params=()):
        return conn.execute(sql, params).fetchone()[0]

    return {
        "evidence_pending_extraction": len(get_evidence_pending_extraction(conn)),
        "extraction_dead": count("SELECT COUNT(*) FROM evidence_extraction WHERE status = 'dead'"),
        "duplicate_evidence": count("SELECT COUNT(*) FROM evidence WHERE duplicate_of IS NOT NULL"),
        "candidates_unmatched": count(
            "SELECT COUNT(*) FROM signal_candidate WHERE topic_id IS NULL AND match_attempts < ?", (max_match_attempts,)),
        "candidates_stuck": count(
            "SELECT COUNT(*) FROM signal_candidate WHERE topic_id IS NULL AND match_attempts >= ?", (max_match_attempts,)),
        "llm_calls_pending": count("SELECT COUNT(*) FROM llm_call WHERE status = 'pending'"),
        "llm_calls_abandoned": count("SELECT COUNT(*) FROM llm_call WHERE status = 'abandoned'"),
        "match_responses_unapplied": count(
            "SELECT COUNT(*) FROM llm_call WHERE stage = 'match' AND status = 'succeeded' AND applied_at IS NULL"),
        "events_unflushed": count("SELECT COUNT(*) FROM event_outbox WHERE emitted_at IS NULL"),
    }


def list_llm_calls(conn, *, stage=None, status=None, limit=20):
    sql, params = ("SELECT call_id, stage, subject_key, model, prompt_fingerprint, status, error, stop_reason, "
                   "input_tokens, output_tokens, started_at, finished_at, applied_at FROM llm_call WHERE 1 = 1"), []
    if stage:
        sql, params = sql + " AND stage = ?", [*params, stage]
    if status:
        sql, params = sql + " AND status = ?", [*params, status]
    rows = conn.execute(sql + " ORDER BY call_id DESC LIMIT ?", (*params, limit)).fetchall()
    return [dict(r) for r in rows]


def llm_usage(conn, *, since=None):
    sql, params = ("SELECT stage, model, COUNT(*) AS calls, COALESCE(SUM(input_tokens), 0) AS input_tokens, "
                   "COALESCE(SUM(output_tokens), 0) AS output_tokens FROM llm_call"), ()
    if since:
        sql, params = sql + " WHERE started_at >= ?", (since,)
    rows = conn.execute(sql + " GROUP BY stage, model ORDER BY stage, model", params).fetchall()
    return [dict(r) for r in rows]


def insert_topic_lineage(conn, *, kind, from_topic_id, to_topic_id, week, reason, decided_by, candidate_ids):
    cursor = conn.execute(
        "INSERT INTO topic_lineage (kind, from_topic_id, to_topic_id, week, reason, decided_by, candidate_ids, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, from_topic_id, to_topic_id, week, reason, decided_by, json.dumps(candidate_ids), utc_now()),
    )
    return cursor.lastrowid


def get_lineage_for_topic(conn, topic_id):
    rows = conn.execute(
        "SELECT * FROM topic_lineage WHERE from_topic_id = ? OR to_topic_id = ? ORDER BY lineage_id", (topic_id, topic_id)
    ).fetchall()
    return [{**dict(r), "candidate_ids": json.loads(r["candidate_ids"])} for r in rows]


def set_topic_status(conn, topic_id, status, merged_into=None):
    conn.execute("UPDATE canonical_topic SET status = ?, merged_into = ? WHERE topic_id = ?", (status, merged_into, topic_id))


def move_candidates(conn, from_topic_id, to_topic_id, candidate_ids=None):
    """Re-point candidates (all of from_topic_id's, or just candidate_ids). original_topic_id is kept."""
    if candidate_ids is None:
        candidate_ids = [r[0] for r in conn.execute(
            "SELECT candidate_id FROM signal_candidate WHERE topic_id = ? ORDER BY candidate_id", (from_topic_id,))]
    for candidate_id in candidate_ids:
        conn.execute("UPDATE signal_candidate SET topic_id = ? WHERE candidate_id = ? AND topic_id = ?",
                     (to_topic_id, candidate_id, from_topic_id))
    return list(candidate_ids)


def get_merge_redirects(conn):
    rows = conn.execute("SELECT topic_id FROM canonical_topic WHERE status = 'merged'").fetchall()
    return {r["topic_id"]: resolve_topic_id(conn, r["topic_id"]) for r in rows}


def triage_audit(conn):
    """Audit-sample triage calls that would have skipped (route=skip at or above their threshold),
    against what the extractor then decided. A 'candidate' outcome is a false skip."""
    rows = conn.execute(
        "SELECT c.subject_key AS evidence_id, x.status AS outcome FROM llm_call c "
        "LEFT JOIN evidence_extraction x ON x.evidence_id = c.subject_key "
        "WHERE c.stage = 'triage' AND c.status = 'succeeded' "
        "AND json_extract(c.request_meta, '$.audit') = 1 "
        "AND json_extract(c.parsed, '$.route') = 'skip' "
        "AND json_extract(c.parsed, '$.confidence') >= json_extract(c.request_meta, '$.threshold') "
        "ORDER BY c.call_id"
    ).fetchall()
    false_ids = [r["evidence_id"] for r in rows if r["outcome"] == "candidate"]
    agreed = sum(r["outcome"] == "skipped" for r in rows)
    decided = agreed + len(false_ids)
    return {
        "audited_skips": len(rows),
        "extractor_agreed": agreed,
        "false_skips": len(false_ids),
        "pending": len(rows) - decided,
        "false_skip_rate": len(false_ids) / decided if decided else None,
        "false_skip_evidence_ids": false_ids,
    }
