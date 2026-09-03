"""HR筛选官 - 评估经历质量并给出改进建议 (纯函数,无 Agent 依赖)"""

import re
from config.prompts.hr_prompt import HR_SYSTEM_PROMPT, HR_EVALUATION_TEMPLATE


def create_hr_agent(model=None, use_thinking: bool = False):
    """创建HR筛选官Agent (向后兼容 — 使用 LLMAgent)"""
    from src.agents.llm_agent import LLMAgent
    return LLMAgent(
        role_name="HR筛选官",
        system_prompt=HR_SYSTEM_PROMPT,
        use_thinking=use_thinking,
    )


def build_hr_evaluation(
    jd: str,
    experience_description: str,
    reference_resumes: list[str] | None = None,
) -> str:
    """构建HR评估任务的提示词，可选参考优秀简历进行对比评估"""
    prompt = HR_EVALUATION_TEMPLATE.format(
        jd=jd,
        experience_description=experience_description,
    )
    if reference_resumes:
        samples_text = "\n---\n".join(
            f"【优秀简历参考 {i+1}】\n{r}"
            for i, r in enumerate(reference_resumes)
        )
        prompt += f"""

## 优秀简历参考样本（作为评分参照标准）
以下是对应岗位的高质量简历样本，请以这些样本的水准作为93分以上的参照标准来评估：
{samples_text}

评分要求：只有达到或超过以上样本水准的经历才能给予93分以上的评分。
"""
    else:
        prompt += """

评分要求：本次目标分数为93分以上。只有经历描述在匹配度、数据化、影响力、简洁度四个维度均接近满分时，才能给予93分以上的"强烈推荐"评分。不要轻易给出高分，要严格把关。
"""
    return prompt


def parse_hr_response(response: str) -> dict:
    """解析HR评估回复，提取结构化数据

    Returns:
        {
            "total_score": int,
            "recommendation": str,
            "match_score": int,
            "data_score": int,
            "impact_score": int,
            "brevity_score": int,
            "strengths": list[str],
            "improvements": list[str],
            "summary": str,
        }
    """
    result = {
        "total_score": 0,
        "recommendation": "",
        "match_score": 0,
        "data_score": 0,
        "impact_score": 0,
        "brevity_score": 0,
        "strengths": [],
        "improvements": [],
        "summary": "",
        "raw": response,
    }

    # 解析总分
    total_match = re.search(r"总体评分.*?(\d+)", response)
    if total_match:
        result["total_score"] = int(total_match.group(1))

    # 解析录用建议
    rec_match = re.search(r"录用建议.*?[：:]\s*(.+)", response)
    if rec_match:
        result["recommendation"] = rec_match.group(1).strip()

    # 解析各维度评分
    dims = {
        "match_score": "匹配度",
        "data_score": "数据化",
        "impact_score": "影响力",
        "brevity_score": "简洁度",
    }
    for key, label in dims.items():
        match = re.search(rf"{label}.*?(\d+)/?25?", response)
        if match:
            result[key] = int(match.group(1))

    # 解析优点
    strengths_match = re.search(r"【优点】[：:]?\s*\n(.*?)(?=【|$)", response, re.DOTALL)
    if strengths_match:
        result["strengths"] = [
            s.strip("- ") for s in strengths_match.group(1).strip().split("\n") if s.strip("- ")
        ]

    # 解析改进点
    improvements_match = re.search(r"【改进点】[：:]?\s*\n(.*?)(?=【|$)", response, re.DOTALL)
    if improvements_match:
        result["improvements"] = [
            s.strip("- ") for s in improvements_match.group(1).strip().split("\n") if s.strip("- ")
        ]

    # 解析HR评语
    summary_match = re.search(r"【HR评语】[：:]\s*(.+)", response)
    if summary_match:
        result["summary"] = summary_match.group(1).strip()

    return result


def build_sequence_evaluation(
    jd: str,
    experiences: list[dict],
    reference_resumes: list[str] | None = None,
) -> str:
    """构建经历序列（组合3-5条经历）的评估提示词

    Args:
        jd: 岗位JD
        experiences: 经历列表，每条 {"title": str, "content": str, "score": int}
        reference_resumes: 优秀简历参考
    """
    exp_text = "\n\n---\n\n".join(
        f"### 经历 {i+1}：{e['title']}（单条评分：{e.get('score', 'N/A')}/100）\n{e['content']}"
        for i, e in enumerate(experiences)
    )

    prompt = f"""请评估以下 {len(experiences)} 条经历组成的工作经历序列对该岗位的整体匹配度。

## 岗位JD
{jd}

## 工作经历序列
{exp_text}

## 评分要求
作为HR，请从整体简历的角度评估这组经历序列：
1. 经历之间的互补性和连贯性
2. 整体是否全面覆盖了JD的核心要求
3. 经历排序是否合理（相关度高的在前）
4. 总体竞争力

输出格式（严格遵守）：
【序列总体评分】：XX/100
【录用建议】：强烈推荐 / 推荐 / 待定 / 不推荐
【覆盖度】：XX/25 - 对JD核心要求的覆盖程度
【互补性】：XX/25 - 各经历之间是否互补、无冗余
【连贯性】：XX/25 - 整体叙事是否流畅
【排序合理度】：XX/25 - 经历排列顺序是否合理
【优点】：
- 优点
【改进点】：
- 改进建议
【HR评语】：一句话总结
"""

    if reference_resumes:
        samples_text = "\n---\n".join(r for r in reference_resumes[:2])
        prompt += f"""

## 优秀简历参考样本（93分参照标准）
{samples_text}
"""
    else:
        prompt += """

评分要求：只有整体竞争力达到顶级水准的序列才能给予93分以上。
"""
    return prompt
