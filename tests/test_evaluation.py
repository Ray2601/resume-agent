"""Phase 2 evaluation harness tests."""

from pathlib import Path

from src.crewai.database import (
    create_experiment, get_experiment, init_db, save_record, update_experiment_progress,
)
from src.evaluation.comparator import compare_experiments
from src.evaluation.dataset import get_eval_case, load_eval_cases, seed_dataset_from_project
from src.evaluation.dataset import EvalCase


def test_seed_and_load_twenty_real_cases(tmp_path):
    root = tmp_path / "project"
    jd_dir = root / "data" / "JD"
    exp_dir = root / "data" / "raw_experiences"
    jd_dir.mkdir(parents=True); exp_dir.mkdir(parents=True)
    for i in range(5):
        (jd_dir / f"jd_{i}.txt").write_text("岗位要求" * 10, encoding="utf-8")
    for i in range(4):
        (exp_dir / f"exp_{i}.md").write_text("真实经历" * 10, encoding="utf-8")
    db_path = str(tmp_path / "eval.db")

    ids = seed_dataset_from_project(root, case_count=20, db_path=db_path)

    assert len(ids) == 20
    assert len(load_eval_cases(db_path=db_path)) == 20
    assert get_eval_case("CASE_001", db_path).case_id == "CASE_001"


def test_compare_experiments_and_detect_regression(tmp_path):
    db_path = str(tmp_path / "compare.db")
    init_db(db_path).close()
    baseline_id = create_experiment("baseline", "writer_v1.0", "hr_v1.0",
                                    "dataset_v1.0", "test-model", 2, db_path)
    candidate_id = create_experiment("candidate", "writer_v1.1", "hr_v1.0",
                                     "dataset_v1.0", "test-model", 2, db_path)
    for experiment_id, scores in ((baseline_id, (94, 80)), (candidate_id, (92, 85))):
        for index, score in enumerate(scores, 1):
            save_record(
                run_id=f"{experiment_id}_{index}", experiment_id=experiment_id,
                case_id=f"CASE_{index:03d}", session_id="eval", jd_text="JD",
                original_exp="experience", optimized_output="output",
                match_score=20, data_score=20, impact_score=20,
                conciseness_score=20, total_score=score, target_score=93,
                writer_prompt_version="writer_v1.1", hr_prompt_version="hr_v1.0",
                db_path=db_path,
            )
        update_experiment_progress(experiment_id, 2, "completed", db_path)

    comparison = compare_experiments(baseline_id, candidate_id, db_path)

    assert comparison["candidate"]["avg_total_score"] > comparison["baseline"]["avg_total_score"]
    assert comparison["cases"][0]["status"] == "regressed"  # pass -> fail overrides -2 stable band
    assert comparison["cases"][1]["status"] == "improved"
    assert get_experiment(candidate_id, db_path)["completed_cases"] == 2


def test_batch_runner_completes_twenty_cases(monkeypatch, tmp_path):
    import src.evaluation.runner as runner

    cases = [EvalCase(f"CASE_{i:03d}", f"case {i}", "其他", "JD", "经历")
             for i in range(1, 21)]
    calls = []

    class FakePipeline:
        def __init__(self, model_id=""):
            self.model_id = model_id

        def run(self, **kwargs):
            calls.append(kwargs)
            return {"run_id": f"run_{len(calls)}"}

    db_path = str(tmp_path / "runner.db")
    init_db(db_path).close()
    monkeypatch.setattr(runner, "load_eval_cases", lambda version, path: cases)
    monkeypatch.setattr(runner, "CrewAIResumePipeline", FakePipeline)

    experiment_id = runner.run_experiment(
        "dataset_v1.0", "writer_v1.1", model_id="test-model", db_path=db_path,
    )

    experiment = get_experiment(experiment_id, db_path)
    assert len(calls) == 20
    assert experiment["status"] == "completed"
    assert experiment["completed_cases"] == 20
    assert calls[0]["case_id"] == "CASE_001"
    assert calls[-1]["experiment_id"] == experiment_id
