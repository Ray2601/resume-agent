"""Aggregate and case-level comparison between two Prompt experiments."""

import json
from statistics import mean

from src.crewai.database import get_experiment, get_experiment_records


def _fabricated(record: dict) -> bool:
    labels = json.loads(record.get("badcase_labels") or "[]")
    return any(item.get("type") in {"fabrication", "hallucination"} for item in labels)


def _has_badcase(record: dict) -> bool:
    return bool(json.loads(record.get("badcase_labels") or "[]"))


def _summary(records: list[dict], target_score: int = 93) -> dict:
    if not records:
        return {key: 0 for key in (
            "total_cases", "avg_total_score", "avg_match_score", "avg_data_score",
            "avg_impact_score", "avg_conciseness_score", "pass_rate",
            "fabrication_rate", "badcase_rate", "avg_iterations", "avg_latency_ms")}
    avg = lambda key: round(mean(row.get(key, 0) or 0 for row in records), 2)
    count = len(records)
    return {
        "total_cases": count,
        "avg_total_score": avg("total_score"), "avg_match_score": avg("match_score"),
        "avg_data_score": avg("data_score"), "avg_impact_score": avg("impact_score"),
        "avg_conciseness_score": avg("conciseness_score"),
        "pass_rate": round(sum((r.get("total_score") or 0) >= (r.get("target_score") or target_score) for r in records) / count, 4),
        "fabrication_rate": round(sum(_fabricated(r) for r in records) / count, 4),
        "badcase_rate": round(sum(_has_badcase(r) for r in records) / count, 4),
        "avg_iterations": avg("iterations"), "avg_latency_ms": avg("latency_ms"),
    }


def compare_experiments(baseline_experiment_id: str, candidate_experiment_id: str,
                        db_path: str | None = None) -> dict:
    baseline_exp = get_experiment(baseline_experiment_id, db_path)
    candidate_exp = get_experiment(candidate_experiment_id, db_path)
    if not baseline_exp or not candidate_exp:
        raise ValueError("Both experiment IDs must exist")
    if baseline_exp["dataset_version"] != candidate_exp["dataset_version"]:
        raise ValueError("Experiments must use the same dataset_version")

    base_records = get_experiment_records(baseline_experiment_id, db_path)
    cand_records = get_experiment_records(candidate_experiment_id, db_path)
    baseline, candidate = _summary(base_records), _summary(cand_records)
    delta = {key: round(candidate[key] - baseline[key], 4)
             for key in baseline if key != "total_cases"}

    base_by_case = {row["case_id"]: row for row in base_records}
    cand_by_case = {row["case_id"]: row for row in cand_records}
    cases = []
    for case_id in sorted(set(base_by_case) | set(cand_by_case)):
        base, cand = base_by_case.get(case_id), cand_by_case.get(case_id)
        if not base or not cand:
            cases.append({"case_id": case_id, "status": "missing", "baseline_score": base and base["total_score"],
                          "candidate_score": cand and cand["total_score"], "score_delta": None})
            continue
        score_delta = cand["total_score"] - base["total_score"]
        baseline_pass = base["total_score"] >= (base.get("target_score") or 93)
        candidate_pass = cand["total_score"] >= (cand.get("target_score") or 93)
        if baseline_pass and not candidate_pass:
            status = "regressed"
        elif score_delta >= 3:
            status = "improved"
        elif score_delta <= -3:
            status = "regressed"
        else:
            status = "stable"
        cases.append({"case_id": case_id, "baseline_score": base["total_score"],
                      "candidate_score": cand["total_score"], "score_delta": score_delta,
                      "baseline_pass": baseline_pass, "candidate_pass": candidate_pass,
                      "status": status})
    return {"baseline": baseline, "candidate": candidate, "delta": delta, "cases": cases}
