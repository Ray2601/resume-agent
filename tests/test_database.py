"""SQLite database module tests."""

import os
import tempfile
from src.crewai.database import (
    init_db, save_record, update_record, get_stats,
    get_badcases, get_recent_records, classify_error, classify_errors,
    save_trace, get_run_traces, track_event, get_events,
)


def test_init_db():
    """Verify DB creation creates all tables."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    try:
        conn = init_db(db_path)
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        assert ("optimization_records",) in tables
        assert ("agent_traces",) in tables
        assert ("events",) in tables
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_classify_error_data_empty():
    etype, sev = classify_error(data_score=10, total_score=75)
    assert etype == "data_empty"
    assert sev == "high"


def test_classify_error_mismatch():
    etype, sev = classify_error(total_score=45)
    assert etype == "mismatch"
    assert sev == "high"


def test_classify_error_timeout():
    etype, sev = classify_error(
        total_score=80, iterations=3, max_iterations=3, status="failed"
    )
    assert etype == "timeout"


def test_classify_error_none():
    etype, sev = classify_error(
        match_score=22, data_score=22, impact_score=22,
        conciseness_score=22, total_score=93,
    )
    assert etype == "none"
    assert sev == "low"


def test_classify_error_hallucination():
    etype, sev = classify_error(
        total_score=85, fabrication_report="编造内容：高 - 发现严重编造"
    )
    assert etype == "hallucination"
    assert sev == "high"


def test_classify_errors_returns_multiple_labels():
    labels = classify_errors(
        match_score=10, data_score=10, impact_score=10,
        conciseness_score=10, total_score=55,
    )
    assert {label for label, _ in labels} >= {
        "mismatch", "data_empty", "low_impact", "star_missing", "keyword_missing",
    }


def test_save_and_query():
    """End-to-end: save a record then query it back."""
    tmpdir = tempfile.mkdtemp()
    db_path = os.path.join(tmpdir, "test.db")
    try:
        conn = init_db(db_path)
        conn.close()

        rid = save_record(
            run_id="run_test_001",
            session_id="test_user",
            jd_text="测试JD",
            original_exp="测试经历",
            optimized_output="优化后经历",
            match_score=20, data_score=18, impact_score=22, conciseness_score=20,
            total_score=80, iterations=2, status="success",
            db_path=db_path,
        )
        assert rid > 0

        update_record(rid, resolved=True, db_path=db_path)

        stats = get_stats(session_id="test_user", db_path=db_path)
        assert stats["total_records"] == 1
        assert stats["avg_score"] == 80.0

        recent = get_recent_records(session_id="test_user", db_path=db_path)
        assert len(recent) == 1
        assert recent[0]["session_id"] == "test_user"
        assert recent[0]["run_id"] == "run_test_001"

        bad = get_badcases(session_id="test_user", max_score=85, db_path=db_path)
        assert len(bad) >= 1

        save_trace("run_test_001", "star_writer", "STAR Writer", 1,
                   "完整输入", "完整输出", latency_ms=123, db_path=db_path)
        traces = get_run_traces("run_test_001", db_path=db_path)
        assert traces[0]["latency_ms"] == 123

        track_event("optimization_completed", "test_user", "run_test_001",
                    {"score": 80}, db_path=db_path)
        events = get_events(run_id="run_test_001", db_path=db_path)
        assert events[0]["event_name"] == "optimization_completed"
    finally:
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
