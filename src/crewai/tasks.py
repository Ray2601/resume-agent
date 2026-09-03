"""CrewAI Task factory functions for resume optimization pipeline."""

from crewai import Task
from src.agents.hr_screener import build_hr_evaluation
from config.prompts.writer_prompt import build_position_prompt


# ================================================================
# Fabrication tolerance guidance builder
# ================================================================

def _build_fabrication_guidance(tolerance: int) -> str:
    """Build fabrication tolerance instructions based on user's setting (0-60%).

    Returns a markdown string to inject into the writer's prompt.
    """
    if tolerance == 0:
        return """## 🚫 数据虚构容忍度：0%（严禁编造）

- 所有数字必须在素材中找到明确来源，不得添加任何素材中不存在的量化数据
- 缺失的数据维度标注【需用户补充：XX】，绝对不要自己编数字
- 你可以合理归纳（如把"面试了20人、选了3人"概括为"组建3人团队"），但不能凭空造数据"""

    elif tolerance <= 15:
        return f"""## ⚠️ 数据虚构容忍度：{tolerance}%（极低）

- 以素材中的真实数据为主，仅在以下情况可以微调：
  - 素材有方向性描述但缺具体数字时，可用行业合理范围估算（如"大幅提升"→"提升约30%"，"显著降低"→"降低约20%"）
  - 素材有部分数据时，可推算关联指标（如已知DAU可推算MAU≈DAU×1.5）
- 核心业务指标（营收、用户数、转化率）不得凭空编造
- 不确定的数据标注【约XX】表示估算"""

    elif tolerance <= 35:
        return f"""## 📊 数据虚构容忍度：{tolerance}%（中等）

- 素材中的真实数据优先使用
- 素材缺失但JD重点关注的指标类型，可基于行业常识和素材中的上下文合理补充
- 补充的数据需满足：①同行业同规模公司的合理范围 ②与其他已有数据逻辑自洽 ③不会引起HR质疑
- 示例：素材说"负责用户增长"但没给具体数字 → 可补充"日均新增用户3000+，次月留存率提升至25%"
- 不要虚构极端数据（如增长1000%、从0做到1亿等明显夸大的数字）"""

    else:
        return f"""## 📈 数据虚构容忍度：{tolerance}%（较高）

- 在素材框架基础上，你可以根据JD要求和行业常识，自由补充合理的量化数据
- 确保数据之间逻辑自洽、符合行业常识、不会在面试中被轻易拆穿
- 虚构数据应遵循：①与素材中提到的项目规模匹配 ②同行业合理范围 ③保留一定的保守性
- 禁止虚构极端异常数据（如行业平均转化率3%你写30%）
- 即使在高容忍度下，也不要编造不存在的项目名、公司名、技术名词"""



# ================================================================
# Step 0: Familiarity Classification
# ================================================================

def create_familiarity_task(agent, jd: str) -> Task:
    """判断JD岗位是否属于已知领域。"""
    return Task(
        description=f"""请判断以下岗位属于"已知领域"还是"陌生领域"。

## JD内容
{jd[:3000]}

请严格按格式输出：【领域判断】、【行业分类】、【置信度】、【判断理由】。""",
        expected_output="领域判断 + 行业分类 + 置信度 + 判断理由",
        agent=agent,
    )


# ================================================================
# Step 0b: Industry Decoding (陌生领域路径)
# ================================================================

def create_industry_learning_task(agent, jd: str) -> Task:
    """为陌生行业构建术语词典和能力映射。"""
    return Task(
        description=f"""请分析以下陌生行业的JD，构建术语词典和能力映射表。

## JD内容
{jd}

请输出：
1. 行业术语词典（行业术语 → 通用含义 → 简历建议用词）
2. 核心硬技能 Top 5
3. 核心软技能 Top 3
4. 行业背景偏好""",
        expected_output="术语词典表格 + 硬技能/软技能列表 + 行业背景偏好",
        agent=agent,
    )


# ================================================================
# Phase 0: JD Analysis
# ================================================================

def create_jd_analysis_task(
    agent, jd: str, reference_resumes: list[str],
    industry_glossary: str = "",
) -> Task:
    """JD分析任务 — 提取关键动作词、数据维度、优先级排序。

    Args:
        industry_glossary: 陌生领域路径的术语词典，注入到分析中
    """
    from config.prompts.deep_writer_prompt import JD_ANALYZER_TEMPLATE

    ref_text = "\n---\n".join(reference_resumes[:3]) if reference_resumes else "（无参考简历）"
    desc = JD_ANALYZER_TEMPLATE.format(jd=jd, reference_resumes=ref_text)

    if industry_glossary:
        desc += f"\n\n## 行业术语参考（来自行业解码顾问）\n{industry_glossary}"

    return Task(
        description=desc,
        expected_output="关键动作词(5-10个)、数据维度(5-8个)、优先级排序前3的能力维度",
        agent=agent,
    )


# ================================================================
# Phase 1: Experience Extraction / Diagnosis
# ================================================================

def create_extraction_task(
    agent, raw_content: str, anchor_content: str = "",
) -> Task:
    """经历诊断提取任务 — 从原始经历提取结构化事实,输出诊断报告。

    诊断报告包含：
    - 项目/产品概述(1句话)
    - 关键工作内容(每条1行)
    - 成果与产出
    - 关键数据(所有原文数字)
    - 缺失项诊断(相对JD欠缺的能力)
    - 可量化点(原文中可补充数据的点)
    - 冗余点(可删除的无关内容)
    """
    from config.prompts.deep_writer_prompt import SMART_EXTRACTOR_TEMPLATE

    content = raw_content[:12000] if len(raw_content) > 12000 else raw_content
    desc = SMART_EXTRACTOR_TEMPLATE.format(
        anchor_content=anchor_content or "（无锚定内容）",
        raw_content=content,
    )

    # 追加诊断要求
    desc += """

## 诊断要求（额外输出）
请在提取事实后，额外输出以下诊断：

### 缺失项
对照JD要求，列出原始经历中缺失的能力/经验/数据维度：
- 缺失项1
- 缺失项2

### 可量化点
原文中有但未明确量化的成果，建议补充具体数字的地方：
- 可量化点1
- 可量化点2

### 冗余点
与目标岗位无关、可删除或压缩的内容：
- 冗余点1
"""

    return Task(
        description=desc,
        expected_output="结构化事实提取(300-500字) + 缺失项/可量化点/冗余点诊断",
        agent=agent,
    )


# ================================================================
# Phase 2: STAR Synthesis + HR Evaluation (iteration)
# ================================================================

def create_synthesis_task(
    agent,
    jd: str,
    phase1_summary: str,
    optimization_focus: str,
    human_rules: str = "",
    industry_glossary: str = "",
    position_category: str = "",
    fabrication_tolerance: int = 0,
    writer_prompt_version: str = "writer_v1.1",
    writer_prompt_content: str | None = None,
) -> Task:
    """STAR撰写任务（首轮）。

    Args:
        human_rules: 用户自定义规则
        industry_glossary: 陌生领域的术语词典
        position_category: 岗位分类，从 position_prompts.py 加载差异化提示词
        fabrication_tolerance: 数据虚构容忍度 0-60，默认0
    """
    from config.prompts.deep_writer_prompt import SYNTHESIZER_TEMPLATE
    from config.prompts.hr_prompt import HR_WRITING_REQUIREMENTS

    position_instructions = build_position_prompt(position_category)
    fabrication_guidance = _build_fabrication_guidance(fabrication_tolerance)

    desc = SYNTHESIZER_TEMPLATE.format(
        jd=jd,
        content_summary=phase1_summary,
        optimization_focus=optimization_focus,
        position_instructions=position_instructions,
        fabrication_guidance=fabrication_guidance,
    )

    # 第一轮生成前就给撰写师完整的 HR 验收口径，减少“先写低分稿再修”的浪费。
    if writer_prompt_content is not None:
        if writer_prompt_content.strip():
            desc += f"\n\n## Prompt版本策略（{writer_prompt_version}）\n{writer_prompt_content}"
    elif writer_prompt_version == "writer_v1.1":
        desc += f"\n\n{HR_WRITING_REQUIREMENTS}"
    elif writer_prompt_version != "writer_v1.0":
        raise ValueError(f"Unsupported writer prompt version: {writer_prompt_version}")

    if industry_glossary:
        desc += f"\n\n## 行业术语参考\n{industry_glossary}\n注意：用以上术语自然润色，但不要编造不存在的行业经验。"

    if human_rules:
        desc += f"\n\n## 用户自定义规则（优先遵守，但不覆盖核心原则）\n{human_rules}"

    # 首轮自反思：要求输出前进行质量自检
    desc += """
## 输出前自检（必须逐项确认后再输出）

在输出最终简历前，请逐项检查并确保全部通过：

1. ✅ 量化数据：每条工作经历是否包含至少2个具体的量化数据？（如百分比、金额、时间、规模等）
2. ✅ JD关键词：是否自然融入了JD中的核心能力关键词？（关键词匹配，不是照抄行业场景）
3. ✅ 强动词开头：每条bullet是否以强动词开头？（主导、设计、推动、实现、优化、搭建、重构、制定、管理等）
4. ✅ STAR完整性：每条经历是否包含 Situation→Task→Action→Result 的完整链条？
5. ✅ 无编造：所有数据、项目名、技术名词是否都能在素材中找到来源？
6. ✅ 字数限制：每条要点中文字数是否 ≤ 85字？（超标必须拆分或精简）

如任一项不满足，请在输出前自动修正。直接输出【优化后】的最终版本，不要解释修改了什么。"""

    return Task(
        description=desc,
        expected_output="【优化后】标签下的STAR格式简历经历描述（已通过HR四维标准和6项自检）",
        agent=agent,
    )


def create_iteration_synthesis_task(
    agent,
    jd: str,
    phase1_summary: str,
    hr_feedback: str,
    previous_version: str,
    improvement_items: str,
    human_rules: str = "",
    industry_glossary: str = "",
    position_category: str = "",
    fabrication_tolerance: int = 0,
    writer_prompt_version: str = "writer_v1.1",
    writer_prompt_content: str | None = None,
) -> Task:
    """STAR迭代撰写任务（第2+轮）。

    Args:
        fabrication_tolerance: 数据虚构容忍度 0-60，默认0
    """
    from config.prompts.deep_writer_prompt import SYNTHESIZER_ITERATION_TEMPLATE

    position_instructions = build_position_prompt(position_category)
    fabrication_guidance = _build_fabrication_guidance(fabrication_tolerance)

    desc = SYNTHESIZER_ITERATION_TEMPLATE.format(
        jd=jd,
        content_summary=phase1_summary,
        hr_feedback=hr_feedback,
        previous_version=previous_version,
        improvement_items=improvement_items,
        position_instructions=position_instructions,
        fabrication_guidance=fabrication_guidance,
    )

    if industry_glossary:
        desc += f"\n\n## 行业术语参考\n{industry_glossary}"

    if human_rules:
        desc += f"\n\n## 用户自定义规则\n{human_rules}"

    if writer_prompt_content:
        desc += f"\n\n## Prompt版本策略（{writer_prompt_version}）\n{writer_prompt_content}"

    return Task(
        description=desc,
        expected_output="根据HR反馈修正后的简历经历描述",
        agent=agent,
    )


def create_hr_evaluation_task(
    agent,
    jd: str,
    experience: str,
    reference_resumes: list[str] | None = None,
) -> Task:
    """HR评分任务 — 四维评分 + 改进建议。"""
    desc = build_hr_evaluation(jd, experience, reference_resumes=reference_resumes or [])
    return Task(
        description=desc,
        expected_output="总体评分、录用建议、4维度评分(匹配度/数据化/影响力/简洁度)、优点列表、改进点列表、HR评语",
        agent=agent,
    )


# ================================================================
# Phase 3: Fabrication Audit
# ================================================================

def create_audit_task(agent, source_material: str, resume: str) -> Task:
    """编造审计任务 — 逐句对比素材和简历。"""
    from config.prompts.deep_writer_prompt import FABRIC_AUDITOR_TEMPLATE

    return Task(
        description=FABRIC_AUDITOR_TEMPLATE.format(
            source_material=source_material,
            resume=resume,
        ),
        expected_output="编造内容清单表格(内容/位置/严重程度) 或 '未发现编造内容'",
        agent=agent,
    )
