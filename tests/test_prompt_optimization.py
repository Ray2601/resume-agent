"""Phase 3 semi-automatic Prompt optimization loop tests."""

import sqlite3

from src.crewai.database import (
    _resolve_db_path, create_experiment, get_experiment, init_db, save_record,
    save_trace, update_experiment_progress,
)
from src.prompt_optimization.clusterer import cluster_badcases
from src.prompt_optimization.patch_generator import generate_patch_proposal, review_patch
from src.prompt_optimization.promotion import promote_candidate, validate_patch
from src.prompt_optimization.root_cause import summarize_root_cause
from src.prompt_optimization.version_manager import create_candidate_version, get_prompt_version


def _save_case(experiment_id, run_id, case_id, score, match, db_path, writer="writer_v1.1"):
    save_record(
        run_id=run_id, experiment_id=experiment_id, case_id=case_id,
        session_id="eval", jd_text="硬件产品经理JD", original_exp="真实经历",
        optimized_output="优化结果", match_score=match, data_score=20,
        impact_score=20, conciseness_score=20, total_score=score,
        target_score=93, writer_prompt_version=writer, hr_prompt_version="hr_v1.0",
        iteration_history=[{"hr_feedback": {"improvements": ["硬件关键词覆盖不足"]}}],
        db_path=db_path,
    )
    save_trace(run_id, "star_writer", "STAR Writer", 1, "input", "output", db_path=db_path)


def test_cluster_root_cause_patch_candidate_and_promotion(tmp_path):
    db_path = str(tmp_path / "phase3.db")
    init_db(db_path).close()
    baseline_id = create_experiment("baseline", "writer_v1.1", "hr_v1.0",
                                    "dataset_v1.0", "test-model", 2, db_path)
    _save_case(baseline_id, "run_b1", "CASE_001", 80, 10, db_path)
    _save_case(baseline_id, "run_b2", "CASE_002", 82, 10, db_path)
    update_experiment_progress(baseline_id, 2, "completed", db_path)

    clusters = cluster_badcases(baseline_id, db_path=db_path)
    keyword_cluster = next(c for c in clusters if c["error_type"] == "keyword_missing")
    root_cause = summarize_root_cause(keyword_cluster["cluster_id"], db_path)
    assert "Possible Root Cause" in root_cause["root_cause_summary"]
    assert root_cause["evidence"]["trace_ids"]

    proposal = generate_patch_proposal(
        keyword_cluster["cluster_id"],
        generator=lambda _: "【优化后的Prompt】：强化有事实支撑的岗位关键词覆盖。",
        db_path=db_path,
    )
    review_patch(proposal["patch_id"], approved=True, db_path=db_path)
    candidate_version = create_candidate_version(proposal["patch_id"], db_path)
    assert candidate_version == "writer_v1.2_candidate"
    assert get_prompt_version("writer_v1.1", db_path)["prompt_content"] != get_prompt_version(candidate_version, db_path)["prompt_content"]

    def fake_run(dataset, writer, hr, model, name, path, **settings):
        candidate_exp = create_experiment(name, writer, hr, dataset, model, 2, path)
        _save_case(candidate_exp, "run_c1", "CASE_001", 95, 24, path, writer)
        _save_case(candidate_exp, "run_c2", "CASE_002", 96, 24, path, writer)
        update_experiment_progress(candidate_exp, 2, "completed", path)
        return candidate_exp

    validation = validate_patch(proposal["patch_id"], baseline_id, db_path,
                                run_fn=fake_run)
    assert validation["decision"] == "PASS"
    assert all(validation["checks"].values())

    final_version = promote_candidate(validation["validation_id"], True, db_path)
    assert final_version == "writer_v1.2"
    assert get_prompt_version(final_version, db_path)["is_active"] == 1
    assert get_prompt_version("writer_v1.1", db_path)["status"] == "archived"


def test_candidate_requires_human_approval(tmp_path):
    db_path = str(tmp_path / "approval.db")
    init_db(db_path).close()
    # Direct DB setup keeps this test focused on the approval gate.
    conn = sqlite3.connect(db_path)
    conn.execute("""INSERT INTO prompt_patch_proposals
        (patch_id, base_prompt_version, target_error_type, cluster_id, patch_content, status)
        VALUES ('patch_draft', 'writer_v1.1', 'data_empty', 'cluster_x', 'new prompt', 'draft')""")
    conn.commit(); conn.close()

    try:
        create_candidate_version("patch_draft", db_path)
        assert False, "draft patch must not create a candidate"
    except ValueError as exc:
        assert "human-approved" in str(exc)


def test_prompt_content_is_database_immutable(tmp_path):
    db_path = str(tmp_path / "immutable.db")
    get_prompt_version("writer_v1.1", db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("UPDATE prompt_versions SET prompt_content = 'mutated' WHERE version = 'writer_v1.1'")
        assert False, "immutable prompt content must reject updates"
    except sqlite3.IntegrityError as exc:
        assert "immutable" in str(exc)
    finally:
        conn.close()
