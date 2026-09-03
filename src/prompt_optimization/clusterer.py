"""Rule-based Badcase clustering using existing multi-label errors."""

import json
import sqlite3
import uuid
from collections import defaultdict
from statistics import mean

from src.crewai.database import _resolve_db_path, get_experiment, init_db


def cluster_badcases(experiment_id: str, baseline_experiment_id: str = "",
                     db_path: str | None = None) -> list[dict]:
    init_db(db_path).close()
    experiment = get_experiment(experiment_id, db_path)
    if not experiment:
        raise ValueError("Experiment does not exist")
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    rows = conn.execute("""SELECT r.case_id, r.total_score, r.writer_prompt_version,
        c.job_type, b.error_type, b.severity
        FROM optimization_records r JOIN run_badcases b ON b.run_id = r.run_id
        LEFT JOIN eval_cases c ON c.case_id = r.case_id
        WHERE r.experiment_id = ?""", (experiment_id,)).fetchall()
    baseline_scores = {}
    if baseline_experiment_id:
        baseline_scores = dict(conn.execute(
            "SELECT case_id, total_score FROM optimization_records WHERE experiment_id = ?",
            (baseline_experiment_id,)).fetchall())
    groups = defaultdict(list)
    for row in rows:
        groups[(row["error_type"], row["job_type"] or "unknown")].append(dict(row))
    result = []
    severity_rank = {"low": 0, "medium": 1, "high": 2}
    for (error_type, job_type), items in groups.items():
        case_ids = sorted({item["case_id"] for item in items})
        deltas = [item["total_score"] - baseline_scores[item["case_id"]]
                  for item in items if item["case_id"] in baseline_scores]
        cluster = {
            "cluster_id": f"cluster_{uuid.uuid4().hex}", "experiment_id": experiment_id,
            "baseline_experiment_id": baseline_experiment_id, "error_type": error_type,
            "prompt_version": experiment["writer_prompt_version"], "job_type": job_type,
            "case_count": len(case_ids), "case_ids": case_ids,
            "avg_score": round(mean(item["total_score"] for item in items), 2),
            "avg_score_delta": round(mean(deltas), 2) if deltas else 0,
            "severity": max((item["severity"] for item in items), key=lambda s: severity_rank[s]),
            "status": "open",
        }
        conn.execute("""INSERT INTO badcase_clusters
            (cluster_id, experiment_id, baseline_experiment_id, error_type, prompt_version,
             job_type, case_count, case_ids_json, avg_score, avg_score_delta, severity, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", (
            cluster["cluster_id"], experiment_id, baseline_experiment_id, error_type,
            cluster["prompt_version"], job_type, cluster["case_count"],
            json.dumps(case_ids, ensure_ascii=False), cluster["avg_score"],
            cluster["avg_score_delta"], cluster["severity"], "open"))
        result.append(cluster)
    conn.commit(); conn.close()
    return sorted(result, key=lambda item: (-item["case_count"], item["error_type"]))
