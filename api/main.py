"""FastAPI backend for Render Free."""

import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from config.prompts.hr_prompt import HR_PROMPT_VERSION
from config.prompts.writer_prompt import WRITER_PROMPT_VERSION
from src.cloud.store import get_cloud_run, save_cloud_result
from src.crewai.pipeline import CrewAIResumePipeline

app = FastAPI(title="Resume Agent API", version="1.0.0")
origin = os.getenv("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware, allow_origins=[origin] if origin != "*" else ["*"],
    allow_credentials=origin != "*", allow_methods=["GET", "POST"], allow_headers=["*"],
)

executor = ThreadPoolExecutor(max_workers=int(os.getenv("WORKER_CONCURRENCY", "1")))
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()


class RunRequest(BaseModel):
    jd: str = Field(min_length=20, max_length=10000)
    original_experience: str = Field(min_length=20, max_length=20000)
    session_id: str = Field(default="web", max_length=100)
    position_category: str = Field(default="互联网产品经理", max_length=100)
    max_iterations: int = Field(default=3, ge=1, le=5)
    fabrication_tolerance: int = Field(default=0, ge=0, le=60)


def _authorize(x_app_token: str = Header(default="")) -> None:
    expected = os.getenv("APP_ACCESS_TOKEN", "")
    if expected and x_app_token != expected:
        raise HTTPException(status_code=401, detail="Invalid app access token")


def _execute(job_id: str, request: RunRequest) -> None:
    sqlite_path = str(Path(tempfile.gettempdir()) / f"resume_agent_{job_id}.db")
    model_id = os.getenv("MODEL_ID_THINKING") or os.getenv("MODEL_ID", "deepseek-reasoner")
    try:
        with jobs_lock:
            jobs[job_id] = {"job_id": job_id, "status": "running"}
        pipeline = CrewAIResumePipeline(model_id=model_id)
        result = pipeline.run(
            jd=request.jd, raw_experience=request.original_experience,
            session_id=request.session_id, db_path=sqlite_path,
            position_category=request.position_category,
            max_iterations=request.max_iterations,
            fabrication_tolerance=request.fabrication_tolerance,
            writer_prompt_version=WRITER_PROMPT_VERSION,
            hr_prompt_version=HR_PROMPT_VERSION, model_id=model_id,
        )
        save_cloud_result(job_id, request.session_id, result, model_id)
        public_result = {
            "job_id": job_id, "run_id": result["run_id"], "status": "completed",
            "final_result": result["final_result"], "final_score": result["final_score"],
            "iterations": result["iterations"], "eval_metrics": result["eval_metrics"],
            "fabrication_report": result["fabrication_report"], "traces": result["traces"],
        }
        with jobs_lock:
            jobs[job_id] = public_result
    except Exception as exc:
        with jobs_lock:
            jobs[job_id] = {"job_id": job_id, "status": "failed", "error": str(exc)}
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
    with jobs_lock:
        jobs[job_id] = {"job_id": job_id, "status": "queued"}
    executor.submit(_execute, job_id, request)
    return jobs[job_id]


@app.get("/api/runs/{job_id}")
def read_run(job_id: str, x_app_token: str = Header(default="")):
    _authorize(x_app_token)
    with jobs_lock:
        current = jobs.get(job_id)
    if current:
        return current
    try:
        persisted = get_cloud_run(job_id)
    except RuntimeError:
        persisted = None
    if persisted:
        return persisted
    raise HTTPException(status_code=404, detail="Run not found")
