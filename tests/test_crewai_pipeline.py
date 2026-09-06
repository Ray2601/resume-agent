"""CrewAI pipeline 单元测试 (Mock LLM)"""

import pytest
from unittest.mock import MagicMock, patch


class TestParseHRResponse:
    """parse_hr_response 解析测试 (无 LLM 依赖)"""

    def test_parse_full_response(self):
        from src.agents.hr_screener import parse_hr_response
        response = """
【总体评分】：85/100
【录用建议】：推荐
【匹配度】：22/25 - 评语A
【数据化】：20/25 - 评语B
【影响力】：23/25 - 评语C
【简洁度】：20/25 - 评语D
【优点】：
- 优点1
- 优点2
【改进点】：
- 改进点1
- 改进点2
【HR评语】：不错
"""
        result = parse_hr_response(response)
        assert result["total_score"] == 85
        assert result["recommendation"] == "推荐"
        assert result["match_score"] == 22
        assert result["data_score"] == 20
        assert result["impact_score"] == 23
        assert result["brevity_score"] == 20
        assert len(result["strengths"]) == 2
        assert len(result["improvements"]) == 2

    def test_parse_minimal_response(self):
        from src.agents.hr_screener import parse_hr_response
        result = parse_hr_response("【总体评分】：50/100")
        assert result["total_score"] == 50
        assert result["strengths"] == []
        assert result["improvements"] == []


class TestBuildHREvaluation:
    """build_hr_evaluation 测试"""

    def test_build_basic_evaluation(self):
        from src.agents.hr_screener import build_hr_evaluation
        result = build_hr_evaluation(jd="测试JD", experience_description="测试经历")
        assert "测试JD" in result
        assert "测试经历" in result


class TestCrewAIResumePipeline:
    """CrewAIResumePipeline 单元测试"""

    def test_pipeline_instantiation(self):
        """验证 pipeline 可正常实例化"""
        from src.crewai.pipeline import CrewAIResumePipeline
        p = CrewAIResumePipeline()
        assert p._familiarity is not None
        assert p._jd_analyst is not None
        assert p._doctor is not None
        assert p._writer is not None
        assert p._hr_scorer is not None
        assert p._auditor is not None
        assert p.reference_resumes == []
        assert p.history == []

    def test_pipeline_jd_cache(self):
        """验证 JD 分析缓存机制"""
        from src.crewai.pipeline import CrewAIResumePipeline
        p = CrewAIResumePipeline()
        jd_text = "测试JD内容"
        cache_key = jd_text[:200]
        p._jd_cache[cache_key] = "缓存的JD分析"
        assert p._jd_cache[cache_key] == "缓存的JD分析"


class TestAgentFactories:
    """Agent 工厂函数测试"""

    def test_create_all_agents(self):
        from src.crewai.agents import (
            create_familiarity_classifier,
            create_industry_decoder,
            create_jd_analyst,
            create_experience_doctor,
            create_star_writer,
            create_hr_scorer,
            create_fabric_auditor,
        )
        agents = [
            create_familiarity_classifier(),
            create_industry_decoder(),
            create_jd_analyst(),
            create_experience_doctor(),
            create_star_writer(),
            create_hr_scorer(),
            create_fabric_auditor(),
        ]
        for agent in agents:
            assert agent.role is not None
            assert agent.goal is not None
            assert agent.llm is not None


def test_agent_empty_response_has_stage_agent_and_call_context():
    from src.crewai.pipeline import CrewAIResumePipeline, PipelineStageError
    pipeline = CrewAIResumePipeline()
    with patch("src.crewai.pipeline.save_trace"):
        with pytest.raises(PipelineStageError) as exc:
            pipeline._trace("phase0_jd_analysis", "JD Analyst", "input", lambda: (_ for _ in ()).throw(ValueError("Invalid response from LLM call - None or empty.")), 0)
    message = str(exc.value)
    assert "stage=phase0_jd_analysis" in message
    assert "agent=JD Analyst" in message
    assert "call_number=1" in message
