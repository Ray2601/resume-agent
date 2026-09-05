"""FastAPI backend for Render."""
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config.prompts.hr_prompt import HR_PROMPT_VERSION
from config.prompts.writer_prompt import WRITER_PROMPT_VERSION
from src.cloud.store import (
    get_cloud_run, list_cloud_runs, save_cloud_failure, save_cloud_job,
    save_cloud_result,
)
from src.crewai.pipeline import CrewAIResumePipeline

app = FastAPI(title="Resume Agent API", version="1.1.0")
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
            jobs[job_id] = {"job_id": job_id, "status": "running", "session_id": request.session_id}
        save_cloud_job(job_id, request, model_id, status="running")
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
        )
        save_cloud_result(job_id, request.session_id, result, model_id, request=request)
        public_result = {
            "job_id": job_id, "run_id": result["run_id"],
            "session_id": request.session_id, "status": "completed",
            "final_result": result["final_result"], "final_score": result["final_score"],
            "iterations": result["iterations"], "eval_metrics": result["eval_metrics"],
            "fabrication_report": result["fabrication_report"],
            "traces": result["traces"],
            "prompt_versions": result.get("prompt_versions", {}),
        }
        with jobs_lock:
            jobs[job_id] = public_result
    except Exception as exc:
        save_cloud_failure(job_id, request.session_id, str(exc), model_id, request=request)
        with jobs_lock:
            jobs[job_id] = {
                "job_id": job_id, "session_id": request.session_id,
                "status": "failed", "error": str(exc),
            }
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
    queued = {"job_id": job_id, "status": "queued", "session_id": request.session_id}
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
        return current
    persisted = get_cloud_run(job_id)
    if persisted:
        return persisted
    raise HTTPException(status_code=404, detail="Run not found")


@app.get("/api/sessions/{session_id}/runs")
def read_session_runs(
    session_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    x_app_token: str = Header(default=""),
):
    _authorize(x_app_token)
    return {"session_id": session_id, "runs": list_cloud_runs(session_id, limit)}
