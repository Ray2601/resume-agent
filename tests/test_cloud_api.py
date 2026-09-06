"""Cloud API tests that do not call an LLM or Neon."""
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from api.main import (
    RunRequest, _authorize, _execute, build_pipeline_experience, health,
    read_session_runs, split_reference_resumes, _job_progress, jobs, jobs_lock,
)


def request(**overrides):
    values = {"jd": "岗位要求" * 5, "original_experience": "真实经历" * 5}
    values.update(overrides)
    return RunRequest(**values)


def test_health():
    assert health() == {"status": "ok"}


def test_access_token_gate():
    with patch.dict(os.environ, {"APP_ACCESS_TOKEN": "secret"}):
        _authorize("secret")
        with pytest.raises(HTTPException) as exc:
            _authorize("wrong")
        assert exc.value.status_code == 401


def test_old_minimal_request_defaults():
    value = request()
    assert value.session_id == "web"
    assert value.max_iterations == 3
    assert value.fabrication_tolerance == 0
    assert value.human_rules == value.related_content == ""
    assert value.reference_resumes == value.anchor_content == ""


@pytest.mark.parametrize("iterations", [0, 11])
def test_max_iterations_range(iterations):
    with pytest.raises(ValidationError):
        request(max_iterations=iterations)


def test_iteration_edges_and_zero_fabrication_tolerance():
    assert request(max_iterations=1, fabrication_tolerance=0).fabrication_tolerance == 0
    assert request(max_iterations=10).max_iterations == 10
    with pytest.raises(ValidationError):
        request(fabrication_tolerance=-1)
    with pytest.raises(ValidationError):
        request(fabrication_tolerance=61)


def test_material_helpers():
    assert build_pipeline_experience("base", "") == "base"
    assert "related" in build_pipeline_experience("base", "related")
    assert split_reference_resumes("") == []
    bundle = "===== REFERENCE RESUME: a.md =====\nA\n\n===== REFERENCE RESUME: b.txt =====\nB"
    refs = split_reference_resumes(bundle)
    assert len(refs) == 2
    assert "A" in refs[0] and "B" in refs[1]


def test_all_fields_reach_pipeline():
    value = request(
        session_id="alice", position_category="硬件产品经理", max_iterations=7,
        fabrication_tolerance=0, human_rules="keep facts",
        related_content="related", reference_resumes="reference",
        anchor_content="anchor",
    )
    result = {
        "run_id": "run-1", "final_result": "done", "final_score": 95,
        "iterations": 2, "eval_metrics": {}, "fabrication_report": "",
        "traces": [], "prompt_versions": {},
    }
    pipeline = MagicMock()
    pipeline.run.return_value = result
    with patch("api.main.CrewAIResumePipeline", return_value=pipeline), \
         patch("api.main.save_cloud_job"), patch("api.main.save_cloud_result"), \
         patch("api.main.save_cloud_failure"):
        _execute("job-1", value)
    kwargs = pipeline.run.call_args.kwargs
    assert kwargs["session_id"] == "alice"
    assert kwargs["position_category"] == "硬件产品经理"
    assert kwargs["max_iterations"] == 7
    assert kwargs["fabrication_tolerance"] == 0
    assert kwargs["human_rules"] == "keep facts"
    assert kwargs["anchor_content"] == "anchor"
    assert "related" in kwargs["raw_experience"]
    assert pipeline.reference_resumes == ["reference"]


def test_session_history_isolation():
    def fake_history(session_id, limit):
        return [{"session_id": session_id}]
    with patch("api.main.list_cloud_runs", side_effect=fake_history):
        alice = read_session_runs("alice", 10, "")
        bob = read_session_runs("bob", 10, "")
    assert alice["runs"] == [{"session_id": "alice"}]
    assert bob["runs"] == [{"session_id": "bob"}]


def test_real_stage_progress_updates_and_iteration():
    value = request(session_id="progress", max_iterations=3)
    job_id = "job-progress-test"
    with patch("api.main.update_cloud_progress") as persist:
        with jobs_lock:
            jobs[job_id] = {"job_id": job_id, "session_id": value.session_id, "stages": [{"key": k, "label": l, "status": "pending"} for k, l in __import__("src.cloud.store", fromlist=["STAGES"]).STAGES]}
        _job_progress(job_id, value, "phase2_writing_iteration", "running", 68, "phase2 iteration 2/3", 2)
        assert jobs[job_id]["current_iteration"] == 2
        assert jobs[job_id]["total_iterations"] == 3
        assert jobs[job_id]["progress_percent"] == 68
        assert jobs[job_id]["stage_status"] == "running"
        persist.assert_called_once()
