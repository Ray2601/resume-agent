"""Persist completed web runs to Neon PostgreSQL."""

import json
import os
from datetime import datetime, timezone


def _connect():
    import psycopg

    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL is required for cloud persistence")
    return psycopg.connect(url)


def init_cloud_db() -> None:
    with _connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS deploy_runs (
            run_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL,
            session_id TEXT NOT NULL, status TEXT NOT NULL,
            writer_prompt_version TEXT, hr_prompt_version TEXT, model_id TEXT,
            final_score INTEGER DEFAULT 0, iterations INTEGER DEFAULT 0,
            eval_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
            final_result TEXT DEFAULT '', fabrication_report TEXT DEFAULT '',
            error_message TEXT DEFAULT '', created_at TIMESTAMPTZ DEFAULT NOW(),
            completed_at TIMESTAMPTZ)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS deploy_agent_traces (
            trace_id BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES deploy_runs(run_id),
            step_name TEXT, agent_name TEXT, iteration INTEGER DEFAULT 0,
            input_summary TEXT, output_summary TEXT, score INTEGER,
            latency_ms INTEGER DEFAULT 0, token_count INTEGER DEFAULT 0,
            status TEXT, created_at TIMESTAMPTZ DEFAULT NOW())""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deploy_traces_run ON deploy_agent_traces(run_id, trace_id)")


def save_cloud_result(job_id: str, session_id: str, result: dict,
                      model_id: str) -> None:
    init_cloud_db()
    versions = result.get("prompt_versions", {})
    with _connect() as conn:
        conn.execute("""INSERT INTO deploy_runs
            (run_id, job_id, session_id, status, writer_prompt_version,
             hr_prompt_version, model_id, final_score, iterations, eval_metrics,
             final_result, fabrication_report, completed_at)
            VALUES (%s,%s,%s,'completed',%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)
            ON CONFLICT (run_id) DO NOTHING""", (
            result["run_id"], job_id, session_id, versions.get("writer"),
            versions.get("hr"), model_id, result.get("final_score", 0),
            result.get("iterations", 0), json.dumps(result.get("eval_metrics", {})),
            result.get("final_result", ""), result.get("fabrication_report", ""),
            datetime.now(timezone.utc),
        ))
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


def get_cloud_run(job_id: str) -> dict | None:
    init_cloud_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM deploy_runs WHERE job_id = %s", (job_id,)).fetchone()
        if not row:
            return None
        columns = [item.name for item in conn.execute("SELECT * FROM deploy_runs LIMIT 0").description]
        data = dict(zip(columns, row))
        traces = conn.execute("""SELECT step_name, agent_name, iteration, score,
            latency_ms, token_count, status FROM deploy_agent_traces
            WHERE run_id = %s ORDER BY trace_id""", (data["run_id"],)).fetchall()
        data["traces"] = [dict(zip(
            ["step_name", "agent_name", "iteration", "score", "latency_ms", "token_count", "status"], item
        )) for item in traces]
        return data
