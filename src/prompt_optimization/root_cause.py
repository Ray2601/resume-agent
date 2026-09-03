"""Evidence-backed, explicitly tentative root-cause summaries."""

import json
import sqlite3
from collections import Counter

from src.crewai.database import _resolve_db_path, get_run_traces, init_db


def summarize_root_cause(cluster_id: str, db_path: str | None = None) -> dict:
    init_db(db_path).close()
    path = db_path or _resolve_db_path(); conn = sqlite3.connect(path); conn.row_factory = sqlite3.Row
    cluster = conn.execute("SELECT * FROM badcase_clusters WHERE cluster_id = ?", (cluster_id,)).fetchone()
    if not cluster:
        conn.close(); raise ValueError("Badcase cluster does not exist")
    case_ids = json.loads(cluster["case_ids_json"])
    placeholders = ",".join("?" for _ in case_ids)
    records = conn.execute(
        f"SELECT run_id, case_id, hr_summary, iteration_history, fabrication_report FROM optimization_records "
        f"WHERE experiment_id = ? AND case_id IN ({placeholders})",
        [cluster["experiment_id"], *case_ids],
    ).fetchall()
    feedback = []
    evidence = {"case_ids": case_ids, "hr_feedback": [], "fact_audits": [], "trace_ids": []}
    for record in records:
        try:
            history = json.loads(record["iteration_history"] or "[]")
            for iteration in history:
                feedback.extend(iteration.get("hr_feedback", {}).get("improvements", []))
        except (json.JSONDecodeError, TypeError):
            pass
        if record["hr_summary"]:
            feedback.append(record["hr_summary"])
        evidence["fact_audits"].append({"case_id": record["case_id"], "summary": record["fabrication_report"][:500]})
        evidence["trace_ids"].extend(trace["trace_id"] for trace in get_run_traces(record["run_id"], db_path))
    common = [text for text, _ in Counter(feedback).most_common(5)]
    evidence["hr_feedback"] = common
    observed = (f"{cluster['case_count']} 个 {cluster['job_type']} Case 命中 "
                f"{cluster['error_type']}，平均分 {cluster['avg_score']:.1f}，"
                f"相对基线变化 {cluster['avg_score_delta']:+.1f}。")
    possible = (f"可能原因：{cluster['prompt_version']} 对 {cluster['error_type']} 相关要求覆盖不足"
                + (f"；共同反馈集中在：{'；'.join(common[:3])}" if common else "；当前证据不足，需人工复核 Trace"))
    confidence = "high" if len(records) >= 5 and len(common) >= 2 else "medium" if len(records) >= 2 else "low"
    summary = f"Observed: {observed}\nPossible Root Cause: {possible}"
    conn.execute("""UPDATE badcase_clusters SET root_cause_summary = ?, evidence_json = ?,
                    confidence = ?, status = 'analyzed' WHERE cluster_id = ?""",
                 (summary, json.dumps(evidence, ensure_ascii=False), confidence, cluster_id))
    conn.commit(); conn.close()
    return {"cluster_id": cluster_id, "root_cause_summary": summary,
            "evidence": evidence, "confidence": confidence}
