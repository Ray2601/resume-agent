"""Durable persistence for deployed web runs in Neon PostgreSQL."""
import json
import os
from datetime import datetime, timezone

from src.crewai.database import classify_errors


def _connect():
    import psycopg
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is required for cloud persistence")
    return psycopg.connect(url)


def _request_dict(request) -> dict:
    if request is None:
        return {}
    if hasattr(request, "model_dump"):
        return request.model_dump()
    if hasattr(request, "dict"):
        return request.dict()
    return dict(request)


def init_cloud_db() -> None:
    """Apply additive, idempotent schema changes; never drops production data."""
    with _connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS deploy_runs (
            run_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL, status TEXT NOT NULL,
            writer_prompt_version TEXT, hr_prompt_version TEXT,
            fact_checker_prompt_version TEXT, model_id TEXT,
            position_category TEXT DEFAULT '',
            max_iterations INTEGER DEFAULT 3,
            fabrication_tolerance INTEGER DEFAULT 0,
            request_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
            final_score INTEGER DEFAULT 0, iterations INTEGER DEFAULT 0,
            eval_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            iteration_history JSONB NOT NULL DEFAULT '[]'::jsonb,
            final_result TEXT DEFAULT '', fabrication_report TEXT DEFAULT '',
            familiarity JSONB NOT NULL DEFAULT '{}'::jsonb,
            industry_glossary TEXT DEFAULT '',
            badcase_labels JSONB NOT NULL DEFAULT '[]'::jsonb,
            error_message TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT NOW(),
            completed_at TIMESTAMPTZ)""")
        additions = {
            "fact_checker_prompt_version": "TEXT",
            "position_category": "TEXT DEFAULT ''",
            "max_iterations": "INTEGER DEFAULT 3",
            "fabrication_tolerance": "INTEGER DEFAULT 0",
            "request_payload": "JSONB NOT NULL DEFAULT '{}'::jsonb",
            "iteration_history": "JSONB NOT NULL DEFAULT '[]'::jsonb",
            "familiarity": "JSONB NOT NULL DEFAULT '{}'::jsonb",
            "industry_glossary": "TEXT DEFAULT ''",
            "badcase_labels": "JSONB NOT NULL DEFAULT '[]'::jsonb",
        }
        for column, definition in additions.items():
            conn.execute(
                f"ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS {column} {definition}"
            )
        conn.execute("""CREATE TABLE IF NOT EXISTS deploy_agent_traces (
            trace_id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES deploy_runs(run_id) ON UPDATE CASCADE,
            step_name TEXT, agent_name TEXT, iteration INTEGER DEFAULT 0,
            input_summary TEXT, output_summary TEXT, score INTEGER,
            latency_ms INTEGER DEFAULT 0, token_count INTEGER DEFAULT 0,
            status TEXT, created_at TIMESTAMPTZ DEFAULT NOW())""")
        conn.execute("""CREATE TABLE IF NOT EXISTS deploy_badcases (
            badcase_id BIGSERIAL PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES deploy_runs(run_id) ON UPDATE CASCADE,
            error_type TEXT NOT NULL, severity TEXT DEFAULT 'medium',
            details JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(run_id, error_type))""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deploy_runs_session ON deploy_runs(session_id, created_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deploy_traces_run ON deploy_agent_traces(run_id, trace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deploy_badcases_run ON deploy_badcases(run_id)")


def save_cloud_job(job_id: str, request, model_id: str, status: str = "queued") -> None:
    init_cloud_db()
    payload = _request_dict(request)
    with _connect() as conn:
        conn.execute("""INSERT INTO deploy_runs
            (run_id, job_id, session_id, status, model_id, position_category,
             max_iterations, fabrication_tolerance, request_payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
            ON CONFLICT (job_id) DO UPDATE SET status=EXCLUDED.status""", (
            job_id, job_id, payload.get("session_id", "web"), status, model_id,
            payload.get("position_category", ""), payload.get("max_iterations", 3),
            payload.get("fabrication_tolerance", 0), json.dumps(payload),
        ))


def save_cloud_result(
    job_id: str, session_id: str, result: dict, model_id: str, request=None
) -> None:
    init_cloud_db()
    versions = result.get("prompt_versions", {})
    metrics = result.get("eval_metrics", {})
    max_iterations = _request_dict(request).get("max_iterations", 3)
    run_status = "success" if result.get("final_score", 0) >= 93 else (
        "timeout" if result.get("iterations", 0) >= max_iterations else "failed"
    )
    labels = [{"type": kind, "severity": severity} for kind, severity in classify_errors(
        match_score=metrics.get("match", 0), data_score=metrics.get("data", 0),
        impact_score=metrics.get("impact", 0),
        conciseness_score=metrics.get("conciseness", 0),
        total_score=metrics.get("total", 0),
        iterations=result.get("iterations", 0), max_iterations=max_iterations,
        status=run_status, fabrication_report=result.get("fabrication_report", ""),
        optimized_output=result.get("final_result", ""),
    )]
    with _connect() as conn:
        conn.execute("""UPDATE deploy_runs SET
            run_id=%s, session_id=%s, status='completed',
            writer_prompt_version=%s, hr_prompt_version=%s,
            fact_checker_prompt_version=%s, model_id=%s,
            final_score=%s, iterations=%s, eval_metrics=%s::jsonb,
            iteration_history=%s::jsonb, final_result=%s,
            fabrication_report=%s, familiarity=%s::jsonb,
            industry_glossary=%s, badcase_labels=%s::jsonb,
            completed_at=%s WHERE job_id=%s""", (
            result["run_id"], session_id, versions.get("writer"),
            versions.get("hr"), versions.get("fact_checker"), model_id,
            result.get("final_score", 0), result.get("iterations", 0),
            json.dumps(result.get("eval_metrics", {})),
            json.dumps(result.get("history", [])),
            result.get("final_result", ""),
            json.dumps(result.get("fabrication_report", ""), ensure_ascii=False)
                if not isinstance(result.get("fabrication_report", ""), str)
                else result.get("fabrication_report", ""),
            json.dumps(result.get("familiarity", {})),
            result.get("industry_glossary", ""), json.dumps(labels),
            datetime.now(timezone.utc), job_id,
        ))
        conn.execute("DELETE FROM deploy_agent_traces WHERE run_id=%s", (result["run_id"],))
        for trace in result.get("traces", []):
            conn.execute("""INSERT INTO deploy_agent_traces
                (run_id, step_name, agent_name, iteration, input_summary,
                 output_summary, score, latency_ms, token_count, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (
                result["run_id"], trace.get("step_name"), trace.get("agent_name"),
                trace.get("iteration", 0), trace.get("input_summary", ""),
                trace.get("output_summary", ""), trace.get("score"),
                trace.get("latency_ms", 0), trace.get("token_count", 0),
                trace.get("status", "success"),
            ))
        for label in labels:
            item = label if isinstance(label, dict) else {"type": str(label)}
            conn.execute("""INSERT INTO deploy_badcases
                (run_id, error_type, severity, details)
                VALUES (%s,%s,%s,%s::jsonb)
                ON CONFLICT (run_id,error_type) DO UPDATE SET
                severity=EXCLUDED.severity, details=EXCLUDED.details""", (
                result["run_id"], item.get("type", "unknown"),
                item.get("severity", "medium"), json.dumps(item),
            ))


def save_cloud_failure(
    job_id: str, session_id: str, message: str, model_id: str, request=None
) -> None:
    try:
        save_cloud_job(job_id, request or {"session_id": session_id}, model_id, "failed")
        with _connect() as conn:
            conn.execute("""UPDATE deploy_runs SET status='failed',
                error_message=%s, completed_at=%s WHERE job_id=%s""",
                (message, datetime.now(timezone.utc), job_id))
    except Exception:
        # Preserve the original pipeline exception if persistence is unavailable.
        pass


def _row_dict(cursor, row) -> dict:
    return dict(zip([item.name for item in cursor.description], row))


def get_cloud_run(job_id: str) -> dict | None:
    init_cloud_db()
    with _connect() as conn:
        cursor = conn.execute("SELECT * FROM deploy_runs WHERE job_id=%s", (job_id,))
        row = cursor.fetchone()
        if not row:
            return None
        data = _row_dict(cursor, row)
        traces = conn.execute("""SELECT step_name,agent_name,iteration,score,
            latency_ms,token_count,status FROM deploy_agent_traces
            WHERE run_id=%s ORDER BY trace_id""", (data["run_id"],))
        names = ["step_name","agent_name","iteration","score","latency_ms","token_count","status"]
        data["traces"] = [dict(zip(names, item)) for item in traces.fetchall()]
        data["error"] = data.pop("error_message", "") or ""
        return data


def list_cloud_runs(session_id: str, limit: int = 20) -> list[dict]:
    init_cloud_db()
    with _connect() as conn:
        cursor = conn.execute("""SELECT job_id,run_id,session_id,status,
            position_category,final_score,iterations,eval_metrics,
            writer_prompt_version,hr_prompt_version,fact_checker_prompt_version,
            error_message,created_at,completed_at
            FROM deploy_runs WHERE session_id=%s
            ORDER BY created_at DESC LIMIT %s""", (session_id, limit))
        columns = [item.name for item in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
