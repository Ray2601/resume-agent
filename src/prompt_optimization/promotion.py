"""Regression validation and explicit human Promote/Reject operations."""

import json
import sqlite3
import uuid
from datetime import datetime

from src.crewai.database import _resolve_db_path, get_experiment_records, init_db
from src.evaluation.comparator import compare_experiments
from src.evaluation.runner import run_experiment
from .version_manager import get_prompt_version


def _error_rate(records: list[dict], error_type: str) -> float:
    if not records:
        return 0.0
    hits = 0
    for record in records:
        labels = json.loads(record.get("badcase_labels") or "[]")
        hits += any(item.get("type") == error_type for item in labels)
    return hits / len(records)


def validate_patch(
    patch_id: str, baseline_experiment_id: str, db_path: str | None = None,
    hallucination_tolerance: float = 0.02, run_fn=run_experiment,
) -> dict:
    """Run the approved candidate on the same dataset and produce a non-binding decision."""
    init_db(db_path).close()
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    patch = conn.execute("SELECT * FROM prompt_patch_proposals WHERE patch_id = ?", (patch_id,)).fetchone()
    baseline = conn.execute("SELECT * FROM experiments WHERE experiment_id = ?", (baseline_experiment_id,)).fetchone()
    if not patch or not patch["candidate_version"]:
        conn.close(); raise ValueError("An approved Candidate Version is required")
    if patch["status"] not in ("approved", "tested") or not baseline:
        conn.close(); raise ValueError("Patch or baseline experiment is not valid")
    candidate_version = patch["candidate_version"]
    get_prompt_version(candidate_version, db_path)
    conn.close()

    base_records = get_experiment_records(baseline_experiment_id, db_path)
    baseline_settings = base_records[0] if base_records else {}
    candidate_experiment_id = run_fn(
        baseline["dataset_version"], candidate_version, baseline["hr_prompt_version"],
        baseline["model_id"], f"validate:{patch_id}", db_path,
        target_score=baseline_settings.get("target_score") or 93,
        max_iterations=baseline_settings.get("max_iterations") or 3,
        fabrication_tolerance=baseline_settings.get("fabrication_tolerance") or 0,
    )
    comparison = compare_experiments(baseline_experiment_id, candidate_experiment_id, db_path)
    cand_records = get_experiment_records(candidate_experiment_id, db_path)
    target = patch["target_error_type"]
    base_target_rate, candidate_target_rate = _error_rate(base_records, target), _error_rate(cand_records, target)
    severe_regressions = sum(
        case["status"] == "regressed" and
        (case.get("baseline_pass") and not case.get("candidate_pass") or (case.get("score_delta") or 0) <= -10)
        for case in comparison["cases"]
    )
    base_hallucination = _error_rate(base_records, "hallucination")
    candidate_hallucination = _error_rate(cand_records, "hallucination")
    checks = {
        "avg_score_not_down": comparison["delta"]["avg_total_score"] >= 0,
        "pass_rate_not_down": comparison["delta"]["pass_rate"] >= 0,
        "hallucination_within_threshold": candidate_hallucination - base_hallucination <= hallucination_tolerance,
        "no_severe_regressions": severe_regressions == 0,
        "target_badcase_rate_down": candidate_target_rate < base_target_rate,
    }
    decision = "PASS" if all(checks.values()) else "REJECT"
    validation_id = f"validation_{uuid.uuid4().hex}"
    conn = sqlite3.connect(path)
    conn.execute("""INSERT INTO promotion_validations
        (validation_id, patch_id, baseline_experiment_id, candidate_experiment_id,
         target_error_type, comparison_json, checks_json, decision)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", (
        validation_id, patch_id, baseline_experiment_id, candidate_experiment_id, target,
        json.dumps(comparison, ensure_ascii=False), json.dumps(checks, ensure_ascii=False), decision))
    conn.execute("UPDATE prompt_patch_proposals SET status = 'tested' WHERE patch_id = ?", (patch_id,))
    conn.commit(); conn.close()
    return {"validation_id": validation_id, "candidate_experiment_id": candidate_experiment_id,
            "decision": decision, "checks": checks, "comparison": comparison,
            "target_badcase_rate": {"baseline": base_target_rate, "candidate": candidate_target_rate},
            "hallucination_rate": {"baseline": base_hallucination, "candidate": candidate_hallucination},
            "severe_regressions": severe_regressions}


def promote_candidate(validation_id: str, human_approved: bool,
                      db_path: str | None = None) -> str:
    """Promote only a passing validation after an explicit human approval."""
    if not human_approved:
        raise ValueError("Explicit human approval is required")
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    validation = conn.execute("SELECT * FROM promotion_validations WHERE validation_id = ?", (validation_id,)).fetchone()
    if not validation or validation["decision"] != "PASS":
        conn.close(); raise ValueError("Only a PASS validation can be promoted")
    patch = conn.execute("SELECT * FROM prompt_patch_proposals WHERE patch_id = ?", (validation["patch_id"],)).fetchone()
    candidate = conn.execute("SELECT * FROM prompt_versions WHERE version = ?", (patch["candidate_version"],)).fetchone()
    final_version = candidate["version"].removesuffix("_candidate")
    if conn.execute("SELECT 1 FROM prompt_versions WHERE version = ?", (final_version,)).fetchone():
        conn.close(); raise ValueError(f"Final version already exists: {final_version}")
    conn.execute("UPDATE prompt_versions SET is_active = 0, status = 'archived' WHERE prompt_type = 'writer' AND is_active = 1")
    conn.execute("""INSERT INTO prompt_versions
        (version, prompt_type, prompt_content, parent_version, source_patch_id,
         status, is_active, promoted_at) VALUES (?, 'writer', ?, ?, ?, 'active', 1, ?)""",
        (final_version, candidate["prompt_content"], candidate["parent_version"],
         patch["patch_id"], datetime.now().isoformat()))
    conn.execute("UPDATE prompt_versions SET status = 'promoted' WHERE version = ?", (candidate["version"],))
    conn.commit(); conn.close(); return final_version


def reject_candidate(patch_id: str, human_approved: bool,
                     db_path: str | None = None) -> None:
    if not human_approved:
        raise ValueError("Explicit human rejection is required")
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    patch = conn.execute("SELECT candidate_version FROM prompt_patch_proposals WHERE patch_id = ?", (patch_id,)).fetchone()
    if not patch:
        conn.close(); raise ValueError("Patch does not exist")
    conn.execute("UPDATE prompt_patch_proposals SET status = 'rejected', reviewed_at = ? WHERE patch_id = ?",
                 (datetime.now().isoformat(), patch_id))
    if patch["candidate_version"]:
        conn.execute("UPDATE prompt_versions SET status = 'rejected', is_active = 0 WHERE version = ?",
                     (patch["candidate_version"],))
    conn.commit(); conn.close()
