"""FastAPI backend for Render."""
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config.prompts.hr_prompt import HR_PROMPT_VERSION
from config.prompts.writer_prompt import WRITER_PROMPT_VERSION
from src.cloud.store import (
    get_cloud_run, list_cloud_runs, save_cloud_failure, save_cloud_job,
    save_cloud_result, update_cloud_progress, mark_stale_cloud_runs_interrupted,
    STAGES, STAGE_ALIASES, _normalize_run_status,
)
from src.crewai.pipeline import CrewAIResumePipeline, PipelineStageError

app = FastAPI(title="Resume Agent API", version="1.2.0")
try:
    mark_stale_cloud_runs_interrupted()
except Exception:
    pass
origins = [
    value.strip() for value in os.getenv("FRONTEND_ORIGIN", "").split(",")
    if value.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=bool(origins),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-App-Token"],
)
executor = ThreadPoolExecutor(max_workers=int(os.getenv("WORKER_CONCURRENCY", "1")))
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
_STAGE_BY_KEY = dict(STAGES)
_STAGE_START = {"step0_classification": 10, "phase0_jd_analysis": 25,
                "phase1_experience_diagnosis": 40, "phase2_writing_iteration": 45,
                "phase3_fabrication_audit": 90}


def _stage_rows():
    return [{"key": key, "label": label, "status": "pending"} for key, label in STAGES]


def _job_progress(job_id, request, key, status="running", percent=0, message="", iteration=0, error=""):
    label = _STAGE_BY_KEY.get(key, key)
    update_cloud_progress(job_id, request.session_id, key, label, status, percent, message,
                          iteration, request.max_iterations, error)
    with jobs_lock:
        current = jobs.setdefault(job_id, {"job_id": job_id, "session_id": request.session_id})
        current.update({"status": "failed" if status == "failed" else current.get("status", "running"),
                        "current_stage": key, "current_stage_label": label,
                        "stage_status": status, "progress_percent": percent,
                        "current_iteration": iteration, "total_iterations": request.max_iterations,
                        "progress_message": message, "heartbeat_at": datetime.now(timezone.utc).isoformat(),
                        "stages": current.get("stages", _stage_rows())})
        current_index = [item[0] for item in STAGES].index(key) if key in [item[0] for item in STAGES] else -1
        for index, row in enumerate(current["stages"]):
            if row["key"] == key:
                row["status"] = status
            elif status == "running" and index < current_index and row["status"] == "pending":
                row["status"] = "completed"


class RunRequest(BaseModel):
    jd: str = Field(min_length=20, max_length=10000)
    original_experience: str = Field(min_length=20, max_length=20000)
    session_id: str = Field(default="web", min_length=1, max_length=100)
    position_category: str = Field(default="互联网产品经理", max_length=100)
    max_iterations: int = Field(default=3, ge=1, le=10)
    fabrication_tolerance: int = Field(default=0, ge=0, le=60)
    human_rules: str = Field(default="", max_length=10000)
    related_content: str = Field(default="", max_length=30000)
    reference_resumes: str = Field(default="", max_length=60000)
    anchor_content: str = Field(default="", max_length=30000)


REFERENCE_MARKER = "===== REFERENCE RESUME:"


def split_reference_resumes(value: str) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    if REFERENCE_MARKER not in text:
        return [text]
    return [part.strip() for part in text.split(REFERENCE_MARKER) if part.strip()]


def build_pipeline_experience(original: str, related: str) -> str:
    related = (related or "").strip()
    suffix = f"\n\n## 关联补充材料\n{related}" if related else ""
    return original.strip() + suffix


def _authorize(x_app_token: str = Header(default="")) -> None:
    expected = os.getenv("APP_ACCESS_TOKEN", "")
    if expected and x_app_token != expected:
        raise HTTPException(status_code=401, detail="Invalid app access token")


def _execute(job_id: str, request: RunRequest) -> None:
    sqlite_path = str(Path(tempfile.gettempdir()) / f"resume_agent_{job_id}.db")
    model_id = os.getenv("MODEL_ID_THINKING") or os.getenv("MODEL_ID", "deepseek-reasoner")
    try:
        with jobs_lock:
            jobs[job_id] = {"job_id": job_id, "status": "running", "session_id": request.session_id,
                            "stage_status": "pending", "progress_percent": 0,
                            "current_iteration": 0, "total_iterations": request.max_iterations,
                            "stages": _stage_rows()}
        save_cloud_job(job_id, request, model_id, status="running")
        last_stage = {"key": None, "iteration": 0}
        def on_progress(step):
            if step == "step0":
                key, pct, msg, it = "step0_classification", 10, "\u6b63\u5728\u8fdb\u884c Step 0 \u00b7 \u9886\u57df\u5206\u7c7b", 0
            elif step == "phase0":
                key, pct, msg, it = "phase0_jd_analysis", 25, "\u6b63\u5728\u8fdb\u884c Phase 0 \u00b7 JD \u5206\u6790", 0
            elif step == "phase1":
                key, pct, msg, it = "phase1_experience_diagnosis", 40, "\u6b63\u5728\u8fdb\u884c Phase 1 \u00b7 \u7ecf\u5386\u8bca\u65ad", 0
            elif step.startswith("phase2_"):
                it = int(step.rsplit("_", 1)[-1])
                key = "phase2_writing_iteration"
                base = 45 + int((it - 1) * 40 / request.max_iterations)
                pct = min(85, base + (int(40 / request.max_iterations) if step.startswith("phase2_score") else 0))
                msg = f"\u6b63\u5728\u8fdb\u884c\u7b2c {it}/{request.max_iterations} \u8f6e\u64b0\u5199\u4e0e HR \u8bc4\u4f30"
            elif step == "phase3":
                key, pct, msg, it = "phase3_fabrication_audit", 90, "\u6b63\u5728\u8fdb\u884c Phase 3 \u00b7 \u7f16\u9020\u5ba1\u8ba1", 0
            else:
                return
            if key != last_stage["key"] or it != last_stage["iteration"]:
                _job_progress(job_id, request, key, "running", pct, msg, it)
                last_stage.update(key=key, iteration=it)
        pipeline = CrewAIResumePipeline(model_id=model_id)
        pipeline.reference_resumes = split_reference_resumes(request.reference_resumes)
        result = pipeline.run(
            jd=request.jd,
            raw_experience=build_pipeline_experience(
                request.original_experience, request.related_content
            ),
            anchor_content=request.anchor_content.strip(),
            session_id=request.session_id,
            db_path=sqlite_path,
            position_category=request.position_category,
            max_iterations=request.max_iterations,
            human_rules=request.human_rules.strip(),
            fabrication_tolerance=request.fabrication_tolerance,
            writer_prompt_version=WRITER_PROMPT_VERSION,
            hr_prompt_version=HR_PROMPT_VERSION,
            model_id=model_id,
            progress_callback=on_progress,
        )
        _job_progress(job_id, request, "phase3_fabrication_audit", "completed", 100,
                      "\u4f18\u5316\u5b8c\u6210", 0)
        with jobs_lock:
            jobs[job_id]["status"] = "completed"
        save_cloud_result(job_id, request.session_id, result, model_id, request=request)
        with jobs_lock:
            progress = dict(jobs.get(job_id, {}))
        public_result = {
            **progress,
            "job_id": job_id, "run_id": result["run_id"],
            "session_id": request.session_id, "status": "completed",
            "stage_status": "completed", "progress_percent": 100,
            "progress_message": "\u4f18\u5316\u5b8c\u6210",
            "final_result": result["final_result"], "final_score": result["final_score"],
            "iterations": result["iterations"], "eval_metrics": result["eval_metrics"],
            "iteration_history": result.get("history", []),
            "fabrication_report": result["fabrication_report"],
            "traces": result["traces"],
            "prompt_versions": result.get("prompt_versions", {}),
        }
        with jobs_lock:
            jobs[job_id] = public_result
    except Exception as exc:
        raw_failed_stage = getattr(exc, "stage", None) or (last_stage.get("key") if "last_stage" in locals() else "")
        current_key = STAGE_ALIASES.get(raw_failed_stage, raw_failed_stage)
        failed_agent = getattr(exc, "agent", "")
        failed_iteration = getattr(exc, "iteration", last_stage.get("iteration", 0) if "last_stage" in locals() else 0)
        if current_key:
            _job_progress(job_id, request, current_key, "failed", 0, "\u4efb\u52a1\u5931\u8d25", failed_iteration, str(exc))
        save_cloud_failure(job_id, request.session_id, str(exc), model_id, request=request,
                           failed_stage=current_key, failed_agent=failed_agent)
        with jobs_lock:
            failed = dict(jobs.get(job_id, {}))
            failed.update({"job_id": job_id, "session_id": request.session_id,
                           "status": "failed", "stage_status": "failed", "error": str(exc),
                           "error_message": str(exc), "failed_stage": current_key,
                           "failed_agent": failed_agent})
            jobs[job_id] = failed
    finally:
        Path(sqlite_path).unlink(missing_ok=True)
        Path(sqlite_path + "-wal").unlink(missing_ok=True)
        Path(sqlite_path + "-shm").unlink(missing_ok=True)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/runs", status_code=202)
def create_run(request: RunRequest, x_app_token: str = Header(default="")):
    _authorize(x_app_token)
    job_id = f"job_{uuid.uuid4().hex}"
    queued = {"job_id": job_id, "status": "queued", "session_id": request.session_id,
              "current_stage": "", "current_stage_label": "", "stage_status": "pending",
              "progress_percent": 0, "current_iteration": 0,
              "total_iterations": request.max_iterations, "progress_message": "\u4efb\u52a1\u6392\u961f\u4e2d",
              "heartbeat_at": None}
    queued["stages"] = _stage_rows()
    with jobs_lock:
        jobs[job_id] = queued
    model_id = os.getenv("MODEL_ID_THINKING") or os.getenv("MODEL_ID", "deepseek-reasoner")
    save_cloud_job(job_id, request, model_id)
    executor.submit(_execute, job_id, request)
    return queued


@app.get("/api/runs/{job_id}")
def read_run(job_id: str, x_app_token: str = Header(default="")):
    _authorize(x_app_token)
    with jobs_lock:
        current = jobs.get(job_id)
    if current:
        return _normalize_run_status(current)
    persisted = get_cloud_run(job_id)
    if persisted:
        return _normalize_run_status(persisted)
    raise HTTPException(status_code=404, detail="Run not found")


@app.get("/api/sessions/{session_id}/runs")
def read_session_runs(
    session_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    x_app_token: str = Header(default=""),
):
    _authorize(x_app_token)
    return {"session_id": session_id, "runs": [_normalize_run_status(run) for run in list_cloud_runs(session_id, limit)]}
