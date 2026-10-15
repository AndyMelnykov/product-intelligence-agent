import json
import sqlite3
from datetime import datetime, timezone

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


# Schema changes after the v0 baseline above. Each entry is (version, function); the function
# runs inside one transaction together with the PRAGMA user_version bump. Never edit SCHEMA_SQL
# or renumber an entry that has shipped: append a new migration instead.
MIGRATIONS = [
    (1, _migration_1_llm_call),
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
                     published_at, title, content, metadata):
    year = captured_at[:4]
    evidence_id = _next_sequence_id(conn, "evidence", "evidence_id", f"EV-{year}-", 6)
    conn.execute(
        "INSERT INTO evidence (evidence_id, source_type, source_name, source_url, "
        "captured_at, published_at, title, content, metadata) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (evidence_id, source_type, source_name, source_url, captured_at, published_at,
         title, content, json.dumps(metadata, sort_keys=True)),
    )
    return evidence_id


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
                             entity=None, effective_date=None):
    evidence = get_evidence(conn, evidence_id)
    if evidence is None:
        raise DBError(f"cannot create signal_candidate: evidence {evidence_id} does not exist")
    year = evidence["captured_at"][:4]
    candidate_id = _next_sequence_id(conn, "signal_candidate", "candidate_id", f"SC-{year}-", 5)
    conn.execute(
        "INSERT INTO signal_candidate (candidate_id, evidence_id, signal_type, topic_id, summary, "
        "confidence, entity, effective_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (candidate_id, evidence_id, signal_type, topic_id, summary, confidence,
         json.dumps(entity, sort_keys=True) if entity is not None else None, effective_date),
    )
    return candidate_id


def set_candidate_topic(conn, candidate_id, topic_id):
    conn.execute("UPDATE signal_candidate SET topic_id = ? WHERE candidate_id = ?", (topic_id, candidate_id))


def get_candidates_without_topic(conn):
    rows = conn.execute(
        "SELECT * FROM signal_candidate WHERE topic_id IS NULL ORDER BY candidate_id"
    ).fetchall()
    return [_signal_candidate_row_to_dict(r) for r in rows]


def get_canonical_topics(conn):
    rows = conn.execute("SELECT * FROM canonical_topic ORDER BY topic_id").fetchall()
    return [_topic_row_to_dict(r) for r in rows]


def get_canonical_topic_by_slug(conn, slug):
    row = conn.execute("SELECT * FROM canonical_topic WHERE slug = ?", (slug,)).fetchone()
    return _topic_row_to_dict(row) if row else None


def insert_canonical_topic(conn, *, slug, name, description, aliases, first_seen, last_seen):
    topic_id = _next_sequence_id(conn, "canonical_topic", "topic_id", "TOPIC-", 4)
    try:
        conn.execute(
            "INSERT INTO canonical_topic (topic_id, slug, name, description, aliases, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (topic_id, slug, name, description, json.dumps(aliases), first_seen, last_seen),
        )
    except sqlite3.IntegrityError as e:
        raise DBError(f"cannot create canonical_topic with slug {slug!r}: {e}") from e
    return topic_id


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
                            change_type, recommended_next_step):
    year = created_at[:4]
    signal_id = _next_sequence_id(conn, "material_signal", "signal_id", f"SIG-{year}-", 4)
    conn.execute(
        "INSERT INTO material_signal (signal_id, created_at, signal_type, topic_id, entity, summary, "
        "confidence_label, materiality_label, materiality_score, materiality_reasons, evidence_ids, "
        "change_type, recommended_next_step) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (signal_id, created_at, signal_type, topic_id, json.dumps(entity, sort_keys=True), summary,
         confidence_label, materiality_label, materiality_score, json.dumps(materiality_reasons),
         json.dumps(evidence_ids), change_type, recommended_next_step),
    )
    return signal_id


def get_material_signal(conn, signal_id):
    row = conn.execute("SELECT * FROM material_signal WHERE signal_id = ?", (signal_id,)).fetchone()
    return _material_signal_row_to_dict(row) if row else None


def get_candidates_for_topic(conn, topic_id):
    rows = conn.execute(
        "SELECT sc.candidate_id, sc.evidence_id, sc.signal_type, sc.summary, sc.confidence, e.captured_at "
        "FROM signal_candidate sc JOIN evidence e ON e.evidence_id = sc.evidence_id "
        "WHERE sc.topic_id = ? ORDER BY sc.candidate_id",
        (topic_id,),
    ).fetchall()
    return [dict(r) for r in rows]
