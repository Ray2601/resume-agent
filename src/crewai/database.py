"""SQLite database for optimization records collection and badcase analysis.

Tables:
  optimization_records — 每次优化运行的完整记录
  templates            — 可复用的JD+经历模板
  cache                — 持久化缓存（JD解析/经历诊断/完整结果）

Error type auto-detection from scores and output patterns:
  data_empty:   data_score < 15
  star_missing: conciseness_score < 15 or output lacks STAR structure
  fabrication:  flagged by Phase 3 audit
  keyword_missing: match_score < 15
  redundant:    conciseness_score < 15 and output length > 800 chars
  mismatch:     match_score < 60 (overall)
  timeout:      iterations hit max without reaching target
  low_impact:   impact_score < 15
"""

import hashlib
import os
import sqlite3
import json
import uuid
from datetime import datetime
from typing import Any

from config.settings import settings
from src.utils.logger import get_logger

logger = get_logger("database")

DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "optimization.db",
)

ERROR_TYPES = {
    "data_empty": "数据空洞（缺少量化指标）",
    "star_missing": "STAR结构缺失",
    "fabrication": "过度捏造（添加了原文没有的事实）",
    "keyword_missing": "JD关键词未融入",
    "redundant": "表达冗余（超过150字/条）",
    "mismatch": "与JD匹配度低于60分",
    "timeout": "迭代达上限未达标",
    "low_impact": "影响力不足（缺乏业务价值体现）",
    "parse_failed": "JSON/响应解析失败",
    "api_error": "API调用异常",
    "hallucination": "幻觉编造（审计确认为高严重度编造）",
    "none": "无错误",
}

# ================================================================
# Database initialization
# ================================================================

def get_db_path() -> str:
    return os.path.join(settings.output_dir, "..", "data", "optimization.db")


def _resolve_db_path() -> str:
    path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def init_db(db_path: str | None = None) -> sqlite3.Connection:
    """Initialize the database and create tables if they don't exist.

    Returns a connection for immediate use. Caller should close it.
    """
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS optimization_records (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id          TEXT    NOT NULL DEFAULT '',
            session_id      TEXT    NOT NULL DEFAULT 'default',
            jd_text         TEXT    NOT NULL,
            original_exp    TEXT    NOT NULL,
            optimized_output TEXT   NOT NULL DEFAULT '',
            match_score     INTEGER DEFAULT 0,
            data_score      INTEGER DEFAULT 0,
            impact_score    INTEGER DEFAULT 0,
            conciseness_score INTEGER DEFAULT 0,
            total_score     INTEGER DEFAULT 0,
            iterations      INTEGER DEFAULT 0,
            status          TEXT    DEFAULT 'pending',
            error_type      TEXT    DEFAULT 'none',
            severity        TEXT    DEFAULT 'low',
            resolved        INTEGER DEFAULT 0,
            hr_recommendation TEXT   DEFAULT '',
            hr_summary      TEXT    DEFAULT '',
            fabrication_report TEXT  DEFAULT '',
            familiarity      TEXT    DEFAULT '',
            is_known_domain  INTEGER DEFAULT 1,
            industry_glossary TEXT   DEFAULT '',
            human_rules      TEXT    DEFAULT '',
            anchor_content   TEXT    DEFAULT '',
            iteration_history TEXT   DEFAULT '[]',
            writer_prompt_version TEXT DEFAULT '',
            hr_prompt_version TEXT DEFAULT '',
            fact_checker_prompt_version TEXT DEFAULT '',
            model_id        TEXT DEFAULT '',
            target_score    INTEGER DEFAULT 93,
            max_iterations  INTEGER DEFAULT 3,
            fabrication_tolerance INTEGER DEFAULT 0,
            latency_ms      INTEGER DEFAULT 0,
            token_total     INTEGER DEFAULT 0,
            badcase_labels  TEXT DEFAULT '[]',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_session
            ON optimization_records(session_id)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_status
            ON optimization_records(status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_error_type
            ON optimization_records(error_type)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_total_score
            ON optimization_records(total_score)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_created_at
            ON optimization_records(created_at)
    """)

    # Incremental migration for databases created before Harness v1.
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(optimization_records)").fetchall()}
    migrations = {
        "run_id": "TEXT NOT NULL DEFAULT ''", "writer_prompt_version": "TEXT DEFAULT ''",
        "hr_prompt_version": "TEXT DEFAULT ''", "fact_checker_prompt_version": "TEXT DEFAULT ''",
        "model_id": "TEXT DEFAULT ''", "target_score": "INTEGER DEFAULT 93",
        "max_iterations": "INTEGER DEFAULT 3", "fabrication_tolerance": "INTEGER DEFAULT 0",
        "latency_ms": "INTEGER DEFAULT 0", "token_total": "INTEGER DEFAULT 0",
        "badcase_labels": "TEXT DEFAULT '[]'",
        "experiment_id": "TEXT DEFAULT ''", "case_id": "TEXT DEFAULT ''",
    }
    for column, definition in migrations.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE optimization_records ADD COLUMN {column} {definition}")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_run_id ON optimization_records(run_id) WHERE run_id != ''")
    conn.execute("""CREATE TABLE IF NOT EXISTS agent_traces (
        trace_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
        step_name TEXT NOT NULL, agent_name TEXT NOT NULL, iteration INTEGER DEFAULT 0,
        input_summary TEXT DEFAULT '', output_summary TEXT DEFAULT '', score INTEGER,
        latency_ms INTEGER DEFAULT 0, token_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'success', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_run ON agent_traces(run_id, trace_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_name TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT 'default', run_id TEXT DEFAULT '',
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, properties_json TEXT DEFAULT '{}')""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, event_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS run_badcases (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
        error_type TEXT NOT NULL, severity TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(run_id, error_type))""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_run_badcases_run ON run_badcases(run_id)")
    conn.execute("""CREATE TABLE IF NOT EXISTS eval_cases (
        case_id TEXT PRIMARY KEY, case_name TEXT, job_type TEXT,
        jd TEXT NOT NULL, original_experience TEXT NOT NULL,
        difficulty TEXT, tags_json TEXT, dataset_version TEXT,
        is_active INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_eval_dataset ON eval_cases(dataset_version, is_active)")
    conn.execute("""CREATE TABLE IF NOT EXISTS experiments (
        experiment_id TEXT PRIMARY KEY, experiment_name TEXT,
        writer_prompt_version TEXT, hr_prompt_version TEXT,
        dataset_version TEXT, model_id TEXT, total_cases INTEGER,
        completed_cases INTEGER DEFAULT 0, status TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, completed_at TIMESTAMP)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_experiment_status ON experiments(status, created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS prompt_versions (
        version TEXT PRIMARY KEY, prompt_type TEXT NOT NULL,
        prompt_content TEXT NOT NULL, parent_version TEXT DEFAULT '',
        source_patch_id TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'candidate',
        is_active INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        promoted_at TIMESTAMP)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_prompt_active ON prompt_versions(prompt_type, is_active)")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS prompt_versions_immutable_content
        BEFORE UPDATE OF version, prompt_type, prompt_content, parent_version, source_patch_id
        ON prompt_versions BEGIN
          SELECT RAISE(ABORT, 'prompt version content is immutable');
        END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS prompt_versions_no_delete
        BEFORE DELETE ON prompt_versions BEGIN
          SELECT RAISE(ABORT, 'prompt versions cannot be deleted');
        END""")
    conn.execute("""CREATE TABLE IF NOT EXISTS badcase_clusters (
        cluster_id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL,
        baseline_experiment_id TEXT DEFAULT '', error_type TEXT NOT NULL,
        prompt_version TEXT NOT NULL, job_type TEXT DEFAULT '', case_count INTEGER,
        case_ids_json TEXT DEFAULT '[]', avg_score REAL DEFAULT 0,
        avg_score_delta REAL DEFAULT 0, severity TEXT DEFAULT 'medium',
        status TEXT DEFAULT 'open', root_cause_summary TEXT DEFAULT '',
        evidence_json TEXT DEFAULT '{}', confidence TEXT DEFAULT 'low',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cluster_experiment ON badcase_clusters(experiment_id, error_type)")
    conn.execute("""CREATE TABLE IF NOT EXISTS prompt_patch_proposals (
        patch_id TEXT PRIMARY KEY, base_prompt_version TEXT NOT NULL,
        target_error_type TEXT NOT NULL, cluster_id TEXT NOT NULL,
        patch_content TEXT NOT NULL, reason TEXT DEFAULT '',
        status TEXT DEFAULT 'draft', candidate_version TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, reviewed_at TIMESTAMP)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS promotion_validations (
        validation_id TEXT PRIMARY KEY, patch_id TEXT NOT NULL,
        baseline_experiment_id TEXT NOT NULL, candidate_experiment_id TEXT NOT NULL,
        target_error_type TEXT NOT NULL, comparison_json TEXT NOT NULL,
        checks_json TEXT NOT NULL, decision TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")

    # Templates table for session history / quick reuse
    conn.execute("""
        CREATE TABLE IF NOT EXISTS templates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT    NOT NULL DEFAULT 'default',
            name            TEXT    NOT NULL,
            jd_text         TEXT    NOT NULL,
            exp_text        TEXT    NOT NULL,
            position_category TEXT   DEFAULT '',
            reference_resumes TEXT  DEFAULT '[]',
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_templates_session
            ON templates(session_id)
    """)

    # Cache table — persistent, shared across sessions
    conn.execute("""
        CREATE TABLE IF NOT EXISTS cache (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            cache_type      TEXT    NOT NULL,
            key             TEXT    NOT NULL,
            result          TEXT    NOT NULL,
            hit_count       INTEGER DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(cache_type, key)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_cache_type_key
            ON cache(cache_type, key)
    """)

    conn.commit()
    logger.info(f"Database initialized: {path}")
    return conn


# ================================================================
# Error type auto-detection
# ================================================================

def classify_error(
    match_score: int = 0,
    data_score: int = 0,
    impact_score: int = 0,
    conciseness_score: int = 0,
    total_score: int = 0,
    iterations: int = 0,
    max_iterations: int = 3,
    status: str = "success",
    fabrication_report: str = "",
    optimized_output: str = "",
) -> tuple[str, str]:
    """Auto-classify error type and severity from scores and output.

    Returns (error_type, severity).
    """
    errors = classify_errors(
        match_score, data_score, impact_score, conciseness_score, total_score,
        iterations, max_iterations, status, fabrication_report, optimized_output,
    )
    return errors[0] if errors else ("none", "low")


def classify_errors(
    match_score: int = 0, data_score: int = 0, impact_score: int = 0,
    conciseness_score: int = 0, total_score: int = 0, iterations: int = 0,
    max_iterations: int = 3, status: str = "success",
    fabrication_report: str = "", optimized_output: str = "",
) -> list[tuple[str, str]]:
    """Return all matching Badcase labels ordered by severity."""
    errors: list[tuple[str, str]] = []

    # Score-based classification
    if total_score < 60:
        errors.append(("mismatch", "high"))
    if data_score < 15 and data_score > 0:
        errors.append(("data_empty", "high"))
    if impact_score < 15 and impact_score > 0:
        errors.append(("low_impact", "medium"))
    if conciseness_score < 15 and conciseness_score > 0:
        # Distinguish redundant vs star_missing
        if len(optimized_output) > 800:
            errors.append(("redundant", "medium"))
        else:
            errors.append(("star_missing", "high"))
    if match_score < 15 and match_score > 0:
        errors.append(("keyword_missing", "medium"))

    # Status-based
    if status == "timeout" or (status != "success" and iterations >= max_iterations and total_score < 93):
        errors.append(("timeout", "high"))
    if status == "api_error":
        errors.append(("api_error", "high"))

    # Fabrication audit
    if fabrication_report and "高" in fabrication_report and "编造" in fabrication_report:
        errors.append(("hallucination", "high"))
    elif fabrication_report and "编造" in fabrication_report and "未发现" not in fabrication_report:
        errors.append(("fabrication", "medium"))

    severity_order = {"high": 0, "medium": 1, "low": 2}
    errors.sort(key=lambda x: severity_order.get(x[1], 2))
    return errors


# ================================================================
# CRUD operations
# ================================================================

def save_record(
    session_id: str = "default",
    jd_text: str = "",
    original_exp: str = "",
    optimized_output: str = "",
    match_score: int = 0,
    data_score: int = 0,
    impact_score: int = 0,
    conciseness_score: int = 0,
    total_score: int = 0,
    iterations: int = 0,
    status: str = "success",
    hr_recommendation: str = "",
    hr_summary: str = "",
    fabrication_report: str = "",
    familiarity: str = "",
    is_known_domain: bool = True,
    industry_glossary: str = "",
    human_rules: str = "",
    anchor_content: str = "",
    iteration_history: list[dict] | None = None,
    max_iterations: int = 3,
    run_id: str = "",
    writer_prompt_version: str = "",
    hr_prompt_version: str = "",
    fact_checker_prompt_version: str = "",
    model_id: str = "",
    target_score: int = 93,
    fabrication_tolerance: int = 0,
    latency_ms: int = 0,
    token_total: int = 0,
    experiment_id: str = "",
    case_id: str = "",
    db_path: str | None = None,
) -> int:
    """Save an optimization record and return the row ID.

    Error type and severity are auto-classified from scores.
    """
    labels = classify_errors(
        match_score=match_score,
        data_score=data_score,
        impact_score=impact_score,
        conciseness_score=conciseness_score,
        total_score=total_score,
        iterations=iterations,
        max_iterations=max_iterations,
        status=status,
        fabrication_report=fabrication_report,
        optimized_output=optimized_output,
    )
    error_type, severity = labels[0] if labels else ("none", "low")
    run_id = run_id or f"run_{uuid.uuid4().hex}"

    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)

    conn.execute("""
        INSERT INTO optimization_records
            (run_id, session_id, jd_text, original_exp, optimized_output,
             match_score, data_score, impact_score, conciseness_score, total_score,
             iterations, status, error_type, severity,
             hr_recommendation, hr_summary, fabrication_report,
             familiarity, is_known_domain, industry_glossary,
             human_rules, anchor_content, iteration_history,
             writer_prompt_version, hr_prompt_version, fact_checker_prompt_version,
             model_id, target_score, max_iterations, fabrication_tolerance,
             latency_ms, token_total, badcase_labels, experiment_id, case_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        run_id, session_id,
        jd_text[:10000],
        original_exp[:10000],
        optimized_output[:10000],
        match_score, data_score, impact_score, conciseness_score, total_score,
        iterations, status, error_type, severity,
        hr_recommendation, hr_summary,
        fabrication_report[:5000] if fabrication_report else "",
        familiarity, 1 if is_known_domain else 0, industry_glossary[:5000] if industry_glossary else "",
        human_rules[:2000] if human_rules else "",
        anchor_content[:3000] if anchor_content else "",
        json.dumps(iteration_history or [], ensure_ascii=False),
        writer_prompt_version, hr_prompt_version, fact_checker_prompt_version,
        model_id, target_score, max_iterations, fabrication_tolerance,
        latency_ms, token_total,
        json.dumps([{"type": t, "severity": s} for t, s in labels], ensure_ascii=False),
        experiment_id, case_id,
    ))

    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.executemany(
        "INSERT OR IGNORE INTO run_badcases(run_id, error_type, severity) VALUES (?, ?, ?)",
        [(run_id, label, severity) for label, severity in labels],
    )
    conn.commit()
    conn.close()

    logger.info(
        f"[DB] Saved record #{row_id} session={session_id} "
        f"score={total_score} error={error_type} severity={severity}"
    )
    return row_id


def save_trace(
    run_id: str, step_name: str, agent_name: str, iteration: int = 0,
    input_summary: str = "", output_summary: str = "", score: int | None = None,
    latency_ms: int = 0, token_count: int = 0, status: str = "success",
    db_path: str | None = None,
) -> int:
    """Persist one agent execution trace. Text is capped only for DB safety."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    cur = conn.execute("""
        INSERT INTO agent_traces
        (run_id, step_name, agent_name, iteration, input_summary, output_summary,
         score, latency_ms, token_count, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (run_id, step_name, agent_name, iteration, input_summary[:20000],
          output_summary[:20000], score, latency_ms, token_count, status))
    conn.commit()
    trace_id = cur.lastrowid
    conn.close()
    return trace_id


def get_run_traces(run_id: str, db_path: str | None = None) -> list[dict]:
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM agent_traces WHERE run_id = ? ORDER BY trace_id", (run_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def track_event(
    event_name: str, session_id: str = "default", run_id: str = "",
    properties: dict | None = None, db_path: str | None = None,
) -> int:
    """Store a product analytics event with JSON properties."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    cur = conn.execute(
        "INSERT INTO events(event_name, session_id, run_id, properties_json) VALUES (?, ?, ?, ?)",
        (event_name, session_id, run_id, json.dumps(properties or {}, ensure_ascii=False)),
    )
    conn.commit()
    event_id = cur.lastrowid
    conn.close()
    return event_id


def get_events(
    run_id: str | None = None, session_id: str | None = None,
    db_path: str | None = None,
) -> list[dict]:
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    query, params = "SELECT * FROM events WHERE 1=1", []
    if run_id:
        query += " AND run_id = ?"
        params.append(run_id)
    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)
    rows = conn.execute(query + " ORDER BY event_id", params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


# ================================================================
# Evaluation datasets and experiments
# ================================================================

def upsert_eval_case(case: dict, db_path: str | None = None) -> None:
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.execute("""INSERT INTO eval_cases
        (case_id, case_name, job_type, jd, original_experience, difficulty,
         tags_json, dataset_version, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(case_id) DO UPDATE SET
          case_name=excluded.case_name, job_type=excluded.job_type, jd=excluded.jd,
          original_experience=excluded.original_experience, difficulty=excluded.difficulty,
          tags_json=excluded.tags_json, dataset_version=excluded.dataset_version,
          is_active=excluded.is_active""", (
        case["case_id"], case.get("case_name", ""), case.get("job_type", ""),
        case["jd"], case["original_experience"], case.get("difficulty", "medium"),
        json.dumps(case.get("tags", []), ensure_ascii=False),
        case.get("dataset_version", "dataset_v1.0"), 1 if case.get("is_active", True) else 0,
    ))
    conn.commit(); conn.close()


def query_eval_cases(dataset_version: str | None = None, active_only: bool = True,
                     db_path: str | None = None) -> list[dict]:
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    query, params = "SELECT * FROM eval_cases WHERE 1=1", []
    if dataset_version:
        query += " AND dataset_version = ?"; params.append(dataset_version)
    if active_only:
        query += " AND is_active = 1"
    rows = conn.execute(query + " ORDER BY case_id", params).fetchall()
    conn.close()
    result = []
    for row in rows:
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json") or "[]")
        item["is_active"] = bool(item["is_active"])
        result.append(item)
    return result


def create_experiment(experiment_name: str, writer_prompt_version: str,
                      hr_prompt_version: str, dataset_version: str, model_id: str,
                      total_cases: int, db_path: str | None = None) -> str:
    experiment_id = f"exp_{uuid.uuid4().hex}"
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path)
    conn.execute("""INSERT INTO experiments
        (experiment_id, experiment_name, writer_prompt_version, hr_prompt_version,
         dataset_version, model_id, total_cases, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'running')""",
        (experiment_id, experiment_name, writer_prompt_version, hr_prompt_version,
         dataset_version, model_id, total_cases))
    conn.commit(); conn.close(); return experiment_id


def update_experiment_progress(experiment_id: str, completed_cases: int,
                               status: str = "running", db_path: str | None = None) -> None:
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path)
    completed_at = datetime.now().isoformat() if status in ("completed", "failed") else None
    conn.execute("""UPDATE experiments SET completed_cases = ?, status = ?,
                    completed_at = COALESCE(?, completed_at) WHERE experiment_id = ?""",
                 (completed_cases, status, completed_at, experiment_id))
    conn.commit(); conn.close()


def get_experiment(experiment_id: str, db_path: str | None = None) -> dict | None:
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)).fetchone()
    conn.close(); return dict(row) if row else None


def get_experiment_records(experiment_id: str, db_path: str | None = None) -> list[dict]:
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM optimization_records WHERE experiment_id = ? ORDER BY case_id",
                        (experiment_id,)).fetchall()
    conn.close(); return [dict(row) for row in rows]


def update_record(
    record_id: int,
    resolved: bool | None = None,
    status: str | None = None,
    error_type: str | None = None,
    severity: str | None = None,
    db_path: str | None = None,
):
    """Update an existing record (e.g., mark as resolved)."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)

    updates = []
    params: list[Any] = []
    if resolved is not None:
        updates.append("resolved = ?")
        params.append(1 if resolved else 0)
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if error_type is not None:
        updates.append("error_type = ?")
        params.append(error_type)
    if severity is not None:
        updates.append("severity = ?")
        params.append(severity)

    if updates:
        params.append(record_id)
        conn.execute(
            f"UPDATE optimization_records SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        conn.commit()

    conn.close()


# ================================================================
# Query functions for badcase analysis
# ================================================================

def get_badcases(
    session_id: str | None = None,
    error_type: str | None = None,
    severity: str | None = None,
    min_score: int = 0,
    max_score: int = 100,
    resolved: bool | None = None,
    limit: int = 50,
    db_path: str | None = None,
) -> list[dict]:
    """Query badcase records with filters."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    query = """
        SELECT * FROM optimization_records
        WHERE total_score BETWEEN ? AND ?
    """
    params: list[Any] = [min_score, max_score]

    if session_id:
        query += " AND session_id = ?"
        params.append(session_id)
    if error_type and error_type != "all":
        query += " AND (error_type = ? OR badcase_labels LIKE ?)"
        params.extend([error_type, f'%"type": "{error_type}"%'])
    if severity:
        query += " AND severity = ?"
        params.append(severity)
    if resolved is not None:
        query += " AND resolved = ?"
        params.append(1 if resolved else 0)

    query += " ORDER BY total_score ASC, created_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_stats(
    session_id: str | None = None,
    db_path: str | None = None,
) -> dict:
    """Get aggregate statistics for dashboard display."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)

    base_where = ""
    params: list[Any] = []
    if session_id:
        base_where = " WHERE session_id = ?"
        params.append(session_id)

    # Total count
    total = conn.execute(
        f"SELECT COUNT(*) FROM optimization_records{base_where}", params
    ).fetchone()[0]

    # Average score
    avg_score = conn.execute(
        f"SELECT AVG(total_score) FROM optimization_records{base_where}", params
    ).fetchone()[0] or 0

    # Multi-label error distribution (fall back to legacy primary label).
    error_dist = {}
    rows = conn.execute(
        f"""
        SELECT error_type, badcase_labels FROM optimization_records{base_where}
        """, params
    ).fetchall()
    for error_type, raw_labels in rows:
        try:
            labels = [item["type"] for item in json.loads(raw_labels or "[]")]
        except (json.JSONDecodeError, TypeError, KeyError):
            labels = []
        if not labels and error_type != "none":
            labels = [error_type]
        for label in set(labels):
            error_dist[label] = error_dist.get(label, 0) + 1

    # Severity distribution
    sev_dist = {}
    rows = conn.execute(
        f"""
        SELECT severity, COUNT(*) as cnt
        FROM optimization_records{base_where}
        GROUP BY severity ORDER BY cnt DESC
        """, params
    ).fetchall()
    for sev, cnt in rows:
        sev_dist[sev] = cnt

    # Resolution rate
    resolved_where = base_where + (" AND resolved = 1" if base_where else " WHERE resolved = 1")
    resolved = conn.execute(
        f"SELECT COUNT(*) FROM optimization_records{resolved_where}",
        params,
    ).fetchone()[0]

    # Badcase count (total_score < 93 or error_type != 'none')
    bad_where = base_where + (" AND (total_score < 93 OR error_type != 'none')" if base_where else " WHERE (total_score < 93 OR error_type != 'none')")
    badcase_count = conn.execute(
        f"SELECT COUNT(*) FROM optimization_records{bad_where}", params
    ).fetchone()[0]

    # Dimension averages
    dims = conn.execute(
        f"""
        SELECT
            AVG(match_score) as avg_match,
            AVG(data_score) as avg_data,
            AVG(impact_score) as avg_impact,
            AVG(conciseness_score) as avg_concise
        FROM optimization_records{base_where}
        """, params
    ).fetchone()

    # Session list
    sessions = conn.execute(
        "SELECT DISTINCT session_id FROM optimization_records ORDER BY session_id"
    ).fetchall()

    conn.close()

    return {
        "total_records": total,
        "badcase_count": badcase_count,
        "avg_score": round(avg_score, 1),
        "error_distribution": error_dist,
        "severity_distribution": sev_dist,
        "resolved_count": resolved,
        "resolution_rate": f"{round(resolved / total * 100, 1)}%" if total > 0 else "0%",
        "avg_dimensions": {
            "match": round(dims[0] or 0, 1),
            "data": round(dims[1] or 0, 1),
            "impact": round(dims[2] or 0, 1),
            "conciseness": round(dims[3] or 0, 1),
        },
        "sessions": [s[0] for s in sessions],
        "error_type_labels": ERROR_TYPES,
    }


def get_recent_records(
    session_id: str | None = None,
    limit: int = 20,
    db_path: str | None = None,
) -> list[dict]:
    """Get most recent records for display."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    if session_id:
        rows = conn.execute(
            """SELECT id, run_id, session_id, total_score, match_score, data_score,
                      impact_score, conciseness_score, iterations, status,
                      error_type, badcase_labels, severity, resolved, familiarity,
                      writer_prompt_version, model_id, latency_ms, created_at
               FROM optimization_records WHERE session_id = ?
               ORDER BY created_at DESC LIMIT ?""",
            (session_id, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, run_id, session_id, total_score, match_score, data_score,
                      impact_score, conciseness_score, iterations, status,
                      error_type, badcase_labels, severity, resolved, familiarity,
                      writer_prompt_version, model_id, latency_ms, created_at
               FROM optimization_records
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


# ================================================================
# Cache CRUD — persistent input/output caching
# ================================================================

def _make_cache_key(text: str) -> str:
    """Generate MD5 hash for text content."""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def get_cached(cache_type: str, text: str, db_path: str | None = None) -> str | None:
    """Get cached result for given cache_type + text content.

    Returns None on miss, the cached result string on hit.
    Auto-increments hit_count.
    """
    path = db_path or _resolve_db_path()
    key = _make_cache_key(text)
    conn = sqlite3.connect(path)
    row = conn.execute(
        "SELECT result FROM cache WHERE cache_type = ? AND key = ?",
        (cache_type, key),
    ).fetchone()
    if row:
        conn.execute(
            "UPDATE cache SET hit_count = hit_count + 1, updated_at = ? WHERE cache_type = ? AND key = ?",
            (datetime.now().isoformat(), cache_type, key),
        )
        conn.commit()
        conn.close()
        logger.info(f"[Cache] HIT  type={cache_type} key={key[:8]}...")
        return row[0]
    conn.close()
    logger.info(f"[Cache] MISS type={cache_type} key={key[:8]}...")
    return None


def set_cached(cache_type: str, text: str, result: str, db_path: str | None = None):
    """Store a result in the persistent cache."""
    path = db_path or _resolve_db_path()
    key = _make_cache_key(text)
    now = datetime.now().isoformat()
    conn = sqlite3.connect(path)
    conn.execute("""
        INSERT INTO cache (cache_type, key, result, hit_count, created_at, updated_at)
        VALUES (?, ?, ?, 0, ?, ?)
        ON CONFLICT(cache_type, key) DO UPDATE SET
            result = excluded.result,
            updated_at = excluded.updated_at
    """, (cache_type, key, result, now, now))
    conn.commit()
    conn.close()
    logger.info(f"[Cache] SET  type={cache_type} key={key[:8]}... ({len(result)} chars)")


def clear_cache(cache_type: str | None = None, db_path: str | None = None):
    """Clear cache entries. If cache_type is None, clears all."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    if cache_type:
        conn.execute("DELETE FROM cache WHERE cache_type = ?", (cache_type,))
        logger.info(f"[Cache] Cleared all entries for type={cache_type}")
    else:
        conn.execute("DELETE FROM cache")
        logger.info("[Cache] Cleared all cache entries")
    conn.commit()
    conn.close()


def get_cache_stats(db_path: str | None = None) -> dict:
    """Get cache statistics for monitoring."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    total_hits = conn.execute("SELECT COALESCE(SUM(hit_count), 0) FROM cache").fetchone()[0]
    rows = conn.execute(
        "SELECT cache_type, COUNT(*) as cnt, COALESCE(SUM(hit_count), 0) as hits FROM cache GROUP BY cache_type"
    ).fetchall()
    conn.close()
    return {
        "total_entries": total,
        "total_hits": total_hits,
        "by_type": {r[0]: {"count": r[1], "hits": r[2]} for r in rows},
    }


# ================================================================
# Template CRUD (session history / quick reuse)
# ================================================================

def save_template(
    session_id: str,
    name: str,
    jd_text: str,
    exp_text: str,
    position_category: str = "",
    reference_resumes: list[str] | None = None,
    db_path: str | None = None,
) -> int:
    """Save a template for later reuse. Returns template ID."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)

    conn.execute("""
        INSERT INTO templates (session_id, name, jd_text, exp_text, position_category, reference_resumes)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        session_id,
        name,
        jd_text[:10000],
        exp_text[:10000],
        position_category,
        json.dumps(reference_resumes or [], ensure_ascii=False),
    ))

    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.commit()
    conn.close()
    logger.info(f"[DB] Saved template #{row_id}: {name}")
    return row_id


def get_templates(
    session_id: str | None = None,
    db_path: str | None = None,
) -> list[dict]:
    """List all templates, optionally filtered by session_id."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    if session_id:
        rows = conn.execute(
            """SELECT id, session_id, name, position_category, created_at
               FROM templates WHERE session_id = ? ORDER BY created_at DESC""",
            (session_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT id, session_id, name, position_category, created_at
               FROM templates ORDER BY created_at DESC"""
        ).fetchall()

    conn.close()
    return [dict(r) for r in rows]


def get_template(
    template_id: int,
    db_path: str | None = None,
) -> dict | None:
    """Load full template content by ID."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row

    row = conn.execute(
        "SELECT * FROM templates WHERE id = ?", (template_id,)
    ).fetchone()

    conn.close()
    if not row:
        return None

    d = dict(row)
    d["reference_resumes"] = json.loads(d.get("reference_resumes", "[]"))
    return d


def delete_template(
    template_id: int,
    db_path: str | None = None,
):
    """Delete a template by ID."""
    path = db_path or _resolve_db_path()
    conn = sqlite3.connect(path)
    conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))
    conn.commit()
    conn.close()
