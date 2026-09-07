"""Durable persistence for deployed web runs in Neon PostgreSQL."""
import json
import os
from datetime import datetime, timezone

from src.crewai.database import classify_errors

STAGE_ALIASES = {"position_classifier": "step0_classification", "industry_decoding": "step0_classification",
                "jd_analysis": "phase0_jd_analysis", "experience_diagnosis": "phase1_experience_diagnosis",
                "star_writer": "phase2_writing_iteration", "hr_reviewer": "phase2_writing_iteration",
                "fact_check": "phase3_fabrication_audit"}

STAGES = [
    ("step0_classification", "Step 0 \u00b7 \u9886\u57df\u5206\u7c7b"),
    ("phase0_jd_analysis", "Phase 0 \u00b7 JD \u5206\u6790"),
    ("phase1_experience_diagnosis", "Phase 1 \u00b7 \u7ecf\u5386\u8bca\u65ad"),
    ("phase2_writing_iteration", "Phase 2 \u00b7 \u64b0\u5199\u8fed\u4ee3"),
    ("phase3_fabrication_audit", "Phase 3 \u00b7 \u7f16\u9020\u5ba1\u8ba1"),
]


def _normalize_run_status(run: dict) -> dict:
    """Return one canonical public status shape for history and detail responses."""
    data = dict(run)
    overall = data.get("status") or "pending"
    stage_keys = {key for key, _ in STAGES}
    by_key = {}
    for stage in data.get("stages") or []:
        if not isinstance(stage, dict):
            continue
        key = STAGE_ALIASES.get(stage.get("key", ""), stage.get("key", ""))
        if key in stage_keys:
            by_key[key] = {**stage, "key": key}

    if overall == "completed":
        data.update({
            "progress_percent": 100,
            "stage_status": "completed",
            "current_stage": "phase3_fabrication_audit",
            "current_stage_label": STAGES[-1][1],
            "failed_stage": None,
            "failed_agent": None,
        })
        data["stages"] = [
            {**by_key.get(key, {}), "key": key, "label": label, "status": "completed"}
            for key, label in STAGES
        ]
        return data

    current_key = STAGE_ALIASES.get(data.get("current_stage", ""), data.get("current_stage", ""))
    if overall in {"failed", "interrupted"}:
        current_key = STAGE_ALIASES.get(data.get("failed_stage") or current_key,
                                        data.get("failed_stage") or current_key)
        if current_key not in stage_keys:
            current_key = next((key for key, stage in by_key.items()
                                if stage.get("status") in {"failed", "interrupted"}), "")
    current_index = next((i for i, (key, _) in enumerate(STAGES) if key == current_key), -1)
    data["stage_status"] = overall if overall in {"failed", "interrupted"} else data.get("stage_status", "pending")
    data["stages"] = [
        {**by_key.get(key, {}), "key": key, "label": label,
         "status": ("completed" if by_key.get(key, {}).get("status") == "completed" or i < current_index
                     else overall if overall in {"failed", "interrupted"} and i == current_index
                     else data.get("stage_status") if i == current_index else "pending")}
        for i, (key, label) in enumerate(STAGES)
    ]
    return data


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
        progress_columns = {
            "current_stage": "TEXT DEFAULT ''", "current_stage_label": "TEXT DEFAULT ''",
            "stage_status": "TEXT DEFAULT 'pending'", "progress_percent": "INTEGER DEFAULT 0",
            "current_iteration": "INTEGER DEFAULT 0", "total_iterations": "INTEGER DEFAULT 3",
            "progress_message": "TEXT DEFAULT ''", "stage_started_at": "TIMESTAMPTZ",
            "heartbeat_at": "TIMESTAMPTZ",
            "failed_stage": "TEXT DEFAULT ''", "failed_agent": "TEXT DEFAULT ''",
        }
        for column, definition in progress_columns.items():
            conn.execute(f"ALTER TABLE deploy_runs ADD COLUMN IF NOT EXISTS {column} {definition}")
        conn.execute("""CREATE TABLE IF NOT EXISTS deploy_run_stages (
            id BIGSERIAL PRIMARY KEY, job_id TEXT NOT NULL, session_id TEXT NOT NULL,
            stage_key TEXT NOT NULL, stage_label TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', iteration INTEGER DEFAULT 0,
            message TEXT DEFAULT '', started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ, error_message TEXT DEFAULT '')""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deploy_run_stages_job ON deploy_run_stages(job_id, id)")
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


def update_cloud_progress(job_id: str, session_id: str, stage_key: str, stage_label: str,
                          stage_status: str, progress_percent: int, message: str = "",
                          iteration: int = 0, total_iterations: int = 3,
                          error_message: str = "") -> None:
    """Persist a real pipeline checkpoint and append its stage history."""
    init_cloud_db()
    now = datetime.now(timezone.utc)
    with _connect() as conn:
        if stage_status == "running":
            conn.execute("""UPDATE deploy_run_stages SET status='completed', completed_at=%s
                WHERE job_id=%s AND status='running'""", (now, job_id))
            conn.execute("""INSERT INTO deploy_run_stages
                (job_id,session_id,stage_key,stage_label,status,iteration,message,started_at)
                VALUES (%s,%s,%s,%s,'running',%s,%s,%s)""",
                (job_id, session_id, stage_key, stage_label, iteration, message, now))
        else:
            conn.execute("""UPDATE deploy_run_stages SET status=%s, message=%s,
                error_message=%s, completed_at=%s WHERE job_id=%s AND stage_key=%s
                AND status='running'""", (stage_status, message, error_message, now, job_id, stage_key))
        conn.execute("""UPDATE deploy_runs SET current_stage=%s,current_stage_label=%s,
            stage_status=%s,progress_percent=%s,current_iteration=%s,total_iterations=%s,
            progress_message=%s,stage_started_at=CASE WHEN %s='running' THEN %s ELSE stage_started_at END,
            heartbeat_at=%s, status=CASE WHEN %s='failed' THEN 'failed' ELSE status END,
            error_message=CASE WHEN %s<>'' THEN %s ELSE error_message END WHERE job_id=%s""",
            (stage_key, stage_label, stage_status, progress_percent, iteration,
             total_iterations, message, stage_status, now, now, stage_status,
             error_message, error_message, job_id))


def mark_stale_cloud_runs_interrupted() -> None:
    """Prevent jobs from previous Render instances remaining running forever."""
    try:
        init_cloud_db()
        with _connect() as conn:
            conn.execute("""UPDATE deploy_runs SET status='interrupted', stage_status='interrupted',
                error_message='\u670d\u52a1\u5b9e\u4f8b\u5df2\u91cd\u542f\uff0c\u4efb\u52a1\u4e2d\u65ad', completed_at=NOW() WHERE status='running'""")
            conn.execute("""UPDATE deploy_run_stages SET status='interrupted',
                error_message='\u670d\u52a1\u5b9e\u4f8b\u5df2\u91cd\u542f\uff0c\u4efb\u52a1\u4e2d\u65ad', completed_at=NOW() WHERE status='running'""")
    except Exception:
        pass


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
    job_id: str, session_id: str, message: str, model_id: str, request=None,
    failed_stage: str = "", failed_agent: str = "",
) -> None:
    try:
        save_cloud_job(job_id, request or {"session_id": session_id}, model_id, "failed")
        with _connect() as conn:
            conn.execute("""UPDATE deploy_runs SET status='failed',
                error_message=%s, failed_stage=%s, failed_agent=%s,
                completed_at=%s WHERE job_id=%s""",
                (message, failed_stage, failed_agent, datetime.now(timezone.utc), job_id))
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
        stages = conn.execute("""SELECT DISTINCT ON (stage_key) stage_key AS key,
            stage_label AS label, status, iteration, message, started_at, completed_at, error_message
            FROM deploy_run_stages WHERE job_id=%s ORDER BY stage_key, id DESC""", (job_id,))
        stage_names = [item.name for item in stages.description]
        latest = {item[0]: dict(zip(stage_names, item)) for item in stages.fetchall()}
        current_key = data.get("current_stage", "") or ""
        current_key = STAGE_ALIASES.get(current_key, current_key)
        current_index = next((i for i, item in enumerate(STAGES) if item[0] == current_key), -1)
        overall = data.get("status", "")
        stage_status = data.get("stage_status", "")
        result_stages = []
        for index, (key, label) in enumerate(STAGES):
            item = latest.get(key, {"key": key, "label": label})
            if overall in ("completed", "success"):
                item["status"] = "completed"
            elif overall in ("failed", "interrupted"):
                item["status"] = "failed" if index == current_index else ("completed" if index < current_index else "pending")
            elif index < current_index:
                item["status"] = "completed"
            elif index == current_index:
                item["status"] = stage_status or item.get("status", "running")
            else:
                item["status"] = "pending"
            item["key"], item["label"] = key, label
            result_stages.append(item)
        data["stages"] = result_stages
        data["error"] = data.pop("error_message", "") or ""
        return _normalize_run_status(data)


def list_cloud_runs(session_id: str, limit: int = 20) -> list[dict]:
    init_cloud_db()
    with _connect() as conn:
        cursor = conn.execute("""SELECT job_id,run_id,session_id,status,
            position_category,final_score,iterations,eval_metrics,
            writer_prompt_version,hr_prompt_version,fact_checker_prompt_version,
            error_message,failed_stage,failed_agent,created_at,completed_at
            FROM deploy_runs WHERE session_id=%s
            ORDER BY created_at DESC LIMIT %s""", (session_id, limit))
        columns = [item.name for item in cursor.description]
        return [_normalize_run_status(dict(zip(columns, row))) for row in cursor.fetchall()]
