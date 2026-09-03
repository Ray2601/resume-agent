"""CrewAI-based multi-agent resume optimization pipeline."""

from src.crewai.pipeline import CrewAIResumePipeline
from src.crewai.agents import (
    create_jd_analyst,
    create_experience_doctor,
    create_star_writer,
    create_hr_scorer,
    create_fabric_auditor,
    create_familiarity_classifier,
    create_industry_decoder,
)
from src.crewai.tasks import (
    create_familiarity_task,
    create_jd_analysis_task,
    create_extraction_task,
    create_industry_learning_task,
    create_synthesis_task,
    create_iteration_synthesis_task,
    create_hr_evaluation_task,
    create_audit_task,
)

__all__ = [
    "CrewAIResumePipeline",
    "create_jd_analyst",
    "create_experience_doctor",
    "create_star_writer",
    "create_hr_scorer",
    "create_fabric_auditor",
    "create_familiarity_classifier",
    "create_industry_decoder",
    "create_familiarity_task",
    "create_jd_analysis_task",
    "create_extraction_task",
    "create_industry_learning_task",
    "create_synthesis_task",
    "create_iteration_synthesis_task",
    "create_hr_evaluation_task",
    "create_audit_task",
]
