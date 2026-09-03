"""Batch runner that reuses CrewAIResumePipeline without duplicating agent logic."""

from config.prompts.hr_prompt import HR_PROMPT_VERSION
from src.crewai.database import (
    create_experiment, init_db, update_experiment_progress,
)
from src.crewai.pipeline import CrewAIResumePipeline
from .dataset import load_eval_cases


def run_experiment(
    dataset_version: str, writer_prompt_version: str,
    hr_prompt_version: str = HR_PROMPT_VERSION, model_id: str = "",
    experiment_name: str = "", db_path: str | None = None,
    target_score: int = 93, max_iterations: int = 3,
    fabrication_tolerance: int = 0, continue_on_error: bool = True,
) -> str:
    cases = load_eval_cases(dataset_version, db_path)
    if not cases:
        raise ValueError(f"No active eval cases for dataset {dataset_version}")

    experiment_id = create_experiment(
        experiment_name or f"{writer_prompt_version}@{dataset_version}",
        writer_prompt_version, hr_prompt_version, dataset_version, model_id,
        len(cases), db_path,
    )
    completed = 0
    try:
        for case in cases:
            try:
                pipeline = CrewAIResumePipeline(model_id=model_id)
                pipeline.run(
                    jd=case.jd, raw_experience=case.original_experience,
                    target_score=target_score, max_iterations=max_iterations,
                    session_id=f"experiment:{experiment_id}", db_path=db_path,
                    position_category=case.job_type,
                    fabrication_tolerance=fabrication_tolerance,
                    writer_prompt_version=writer_prompt_version,
                    hr_prompt_version=hr_prompt_version,
                    experiment_id=experiment_id, case_id=case.case_id,
                    model_id=model_id,
                )
                completed += 1
                update_experiment_progress(experiment_id, completed, "running", db_path)
            except Exception:
                if not continue_on_error:
                    raise
        status = "completed" if completed == len(cases) else "failed"
        update_experiment_progress(experiment_id, completed, status, db_path)
    except Exception:
        update_experiment_progress(experiment_id, completed, "failed", db_path)
        raise
    return experiment_id
