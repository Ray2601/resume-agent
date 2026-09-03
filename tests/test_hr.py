"""测试HR筛选官相关功能"""

from src.agents.hr_screener import build_hr_evaluation, parse_hr_response


class TestHRFunctions:
    def test_build_hr_evaluation(self):
        result = build_hr_evaluation(
            jd="测试JD内容",
            experience_description="测试经历描述",
        )
        assert "测试JD内容" in result
        assert "测试经历描述" in result

    def test_parse_hr_response_full(self):
        response = """
【总体评分】：78/100
【录用建议】：推荐
【匹配度】：20/25 - 评语A
【数据化】：18/25 - 评语B
【影响力】：22/25 - 评语C
【简洁度】：18/25 - 评语D
【优点】：
- 优点1
- 优点2
【改进点】：
- 改进点1
- 改进点2
【HR评语】：一份不错的经历描述
"""
        result = parse_hr_response(response)
        assert result["total_score"] == 78
        assert result["recommendation"] == "推荐"
        assert result["match_score"] == 20
        assert result["data_score"] == 18
        assert result["impact_score"] == 22
        assert result["brevity_score"] == 18
        assert len(result["strengths"]) == 2
        assert len(result["improvements"]) == 2
        assert "不错的经历" in result["summary"]

    def test_parse_hr_response_minimal(self):
        response = "【总体评分】：50/100"
        result = parse_hr_response(response)
        assert result["total_score"] == 50
        assert result["strengths"] == []
        assert result["improvements"] == []
