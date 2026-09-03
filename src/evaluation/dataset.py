"""Read and seed fixed evaluation datasets."""

from dataclasses import dataclass, field
from pathlib import Path

from src.crewai.database import init_db, query_eval_cases, upsert_eval_case


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    case_name: str
    job_type: str
    jd: str
    original_experience: str
    difficulty: str = "medium"
    tags: list[str] = field(default_factory=list)
    dataset_version: str = "dataset_v1.0"
    is_active: bool = True


def _to_case(row: dict) -> EvalCase:
    return EvalCase(**{key: row[key] for key in EvalCase.__dataclass_fields__})


def load_eval_cases(dataset_version: str = "dataset_v1.0",
                    db_path: str | None = None) -> list[EvalCase]:
    init_db(db_path).close()
    return [_to_case(row) for row in query_eval_cases(dataset_version, True, db_path)]


def get_dataset_cases(dataset_version: str = "dataset_v1.0",
                      db_path: str | None = None) -> list[EvalCase]:
    return load_eval_cases(dataset_version, db_path)


def get_eval_case(case_id: str, db_path: str | None = None) -> EvalCase | None:
    init_db(db_path).close()
    rows = query_eval_cases(None, False, db_path)
    row = next((item for item in rows if item["case_id"] == case_id), None)
    return _to_case(row) if row else None


def seed_dataset_from_project(
    project_root: str | Path, dataset_version: str = "dataset_v1.0",
    case_count: int = 20, db_path: str | None = None,
) -> list[str]:
    """Build deterministic cases from real JD/experience files already in the project."""
    root = Path(project_root)
    jd_files = sorted((root / "data" / "JD").glob("*.txt"))
    exp_files = sorted(p for p in (root / "data" / "raw_experiences").rglob("*")
                       if p.is_file() and p.suffix.lower() in {".txt", ".md"})
    pairs = [(jd_file, exp_file) for jd_file in jd_files for exp_file in exp_files]
    if len(pairs) < case_count:
        raise ValueError(f"Only {len(pairs)} real JD/experience pairs available; need {case_count}")

    init_db(db_path).close()
    ids = []
    for index, (jd_file, exp_file) in enumerate(pairs[:case_count], 1):
        jd = jd_file.read_text(encoding="utf-8")
        experience = exp_file.read_text(encoding="utf-8")
        difficulty = "hard" if len(experience) > 15000 else "medium" if len(experience) > 5000 else "easy"
        case_id = f"CASE_{index:03d}"
        upsert_eval_case({
            "case_id": case_id,
            "case_name": f"{jd_file.stem} × {exp_file.stem}",
            "job_type": jd_file.stem,
            "jd": jd,
            "original_experience": experience,
            "difficulty": difficulty,
            "tags": ["real_project_data", "long_text"] if difficulty == "hard" else ["real_project_data"],
            "dataset_version": dataset_version,
        }, db_path)
        ids.append(case_id)
    return ids
